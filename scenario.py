#!/usr/bin/env python3

import os
import yaml
import torch
import triton
import typing
import transformers

import langchain
import langchain.llms
import langchain.chains
import langchain.schema
import langchain.prompts
import langchain.callbacks

class LLM():
    def __init__(self, source_name=None, model_name=None, menu=None):

        # Select large language model
        default_source_name = 'llamacpp'
        default_model_name = 'mistral-7b-openorca'
        default_menu = os.path.join(os.path.split(__file__)[0], 'llms.yaml')

        self.menu = menu if menu is not None else default_menu
        model_paths = yaml.safe_load(open(self.menu, 'r'))
        if source_name is not None and model_name is not None:
            self.source_name = source_name
            self.model_name = model_name
        else:
            self.source_name = default_source_name
            self.model_name = default_model_name
        self.model_path = model_paths[self.source_name][self.model_name]

        if self.source_name == 'openai':

            # Model Source: OpenAI (Cloud)
            cbm = langchain.callbacks.manager.CallbackManager(
                [langchain.callbacks.streaming_stdout\
                 .StreamingStdOutCallbackHandler()])
            self.llm = langchain.llms.OpenAI(
                model_name=self.model_name,
                callback_manager=cbm,
                streaming=True,
            )
            self.bound = {}

        elif self.source_name == 'llamacpp':

            # Model Source: llama.cpp (Local)
            cbm = langchain.callbacks.manager.CallbackManager(
                [langchain.callbacks.streaming_stdout\
                 .StreamingStdOutCallbackHandler()])
            self.llm = langchain.llms.LlamaCpp(
                model_path=self.model_path,
                n_gpu_layers=-1,
                max_tokens=1000,
                n_batch=512,
                n_ctx=8192,
                f16_kv=True,
                callback_manager=cbm,
                verbose=False,
            )
            self.bound = {'stop': '""'}

        elif self.source_name == 'huggingface':

            # Model Source: Hugging Face (Local)
            device = torch.device('cuda:' + str(torch.cuda.current_device())
                                  if torch.cuda.is_available() else 'cpu')
            config = transformers.AutoConfig.from_pretrained(
                self.model_path,
                trust_remote_code=True,
                init_device='cuda',
                learned_pos_emb=False,
                max_seq_len=2500,
            )
            # config.attn_config['attn_impl'] = 'torch'

            model = transformers.AutoModelForCausalLM.from_pretrained(
                self.model_path,
                config=config,
                trust_remote_code=True,
                torch_dtype=torch.bfloat16,
            )
            model.eval()
            model.to(device)

            tokenizer = transformers.AutoTokenizer.from_pretrained(
                self.model_path)

            class StopOnTokens(transformers.StoppingCriteria):
                def __call__(self, input_ids: torch.LongTensor,
                             scores: torch.FloatTensor,
                             **kwargs: typing.Any) -> bool:
                    for stop_id in tokenizer.convert_tokens_to_ids(
                            ['<|endoftext|>', '<|im_end|>']):
                        if input_ids[0][-1] == stop_id:
                            return True
                    return False
            stopping_criteria = transformers.StoppingCriteriaList(
                [StopOnTokens()])

            streamer = transformers.TextStreamer(
                tokenizer, skip_prompt=True, skip_special=True)

            pipeline = transformers.pipeline(
                'text-generation',
                model=model,
                tokenizer=tokenizer,
                device=device,
                max_new_tokens=2048,
                stopping_criteria=stopping_criteria,
                streamer=streamer,
            )

            self.llm = langchain.llms.HuggingFacePipeline(
                pipeline=pipeline,
            )
            self.bound = {}


class History():
    def __init__(self):
        self.entries = []
    def add(self, name, text):
        self.entries.append({'name': name, 'text': text})
    def str(self, name=None):
        return ''.join([('You' if entry['name'] == name else entry['name'])
                        + ':\n"""\n' + entry['text'] + '\n"""\n'
                        for entry in self.entries])
    def textonly(self):
        return '"""\n' \
            + '\n\n'.join([entry['text'] for entry in self.entries]) \
            + '\n"""'
    def copy(self):
        history_copy = History()
        history_copy.entries = self.entries.copy()
        return history_copy


class Control():
    def __init__(self, llm_source_name=None, llm_model_name=None):
        self.llm = LLM(source_name=llm_source_name, model_name=llm_model_name)
        self.public_history = History()

    def run(self):
        raise Exception('! Override this method in the subclass for your specific scenario.')

    def header(self, title, h=0, width=80):
        print()
        if h == 0:
            print('+-' + '-' * min(len(title), width) + '-+')
            print('| ' + title + ' |')
            print('+-' + '-' * min(len(title), width) + '-+')
        elif h == 1:
            print('-' * min(len(title), width))
            print(title)
            print('-' * min(len(title), width))
        else:
            print(title)

    def record_narration(self, narration):
        self.public_history.add('Narrator', narration)

    def record_response(self, player_name, player_response):
        self.public_history.add(player_name, player_response)

    def adjudicate(self, history=None, responses=None, query=None, verbose=0):
        if query is None:
            #query = 'This is an example of what happens next as a result of these plans.'
            query = 'This is what happens when these plans are carried out'

        # Define template and included variables
        template = ''
        variables = {}
        if history is not None:
            template += 'This is what has happened so far:\n{history}\n'
            variables['history'] = history.textonly()
        if responses is not None:
            #template += 'These are the actions undertaken by each person or group:\n{responses}\n'
            template += 'These are the plans for each person or group:\n{responses}\n'
            variables['responses'] = responses.str()
        #template += 'Question:\n"""\n{query}\n"""\nAnswer:\n"""\n'
        template += '{query}:\n"""\n'
        variables['query'] = query

        prompt = langchain.prompts.PromptTemplate(
            template=template,
            input_variables=list(variables.keys()),
        )
        chain = prompt | self.llm.llm.bind(**self.llm.bound)
        if verbose >= 2:
            print(prompt.format(**variables))
            print()
        output = chain.invoke(variables).strip()
        print()
        return output

    def assess(self, history, query):
        template = 'This is what happened.\n{history}\nQuestion: {query}\nAnswer: '
        prompt = langchain.prompts.PromptTemplate(
            template=template,
            input_variables=['history', 'query'],
        )
        chain = prompt | self.llm.llm.bind(**self.llm.bound)
        output = chain.invoke({
            'history': history.str(), 'query': query}).strip()
        print()
        return output

    def chat(self):
        conversation = langchain.chains.ConversationChain(
            llm=self.llm.llm,
            memory=langchain.memory.ConversationBufferMemory())
        while True:
            line = input('\n')
            if line.lower() == 'exit':
                break
            conversation(line)


class Team():
    def __init__(self, name='Anonymous', leader=None, members=None):
        self.name = name
        self.leader = leader
        self.members = members

    def respond(self, history=None, query=None, verbose=0):
        member_responses = History()
        for member in self.members:
            if verbose >= 1:
                print('\n### ' + member.name)
            member_responses.add(member.name, member.respond(
                history=history, query=query, verbose=verbose))
        if verbose >= 1:
            print('\n### ' + self.leader.name)
        leader_response = self.leader.synthesize(
            history=history, responses=member_responses, verbose=verbose)
        return leader_response

    def synthesize(self, history=None, responses=None, verbose=0):
        return self.leader.synthesize(
            history=history, responses=responses, verbose=verbose)

    def info(self, verbose=0, offset=0):
        print(' ' * offset + 'Team:', self.name)
        print(' ' * offset + '  Leader:', self.leader.name)
        print(' ' * offset + '  Members:', [member.name for member in self.members])
        if verbose >= 1:
            for member in self.members:
                member.info(verbose=verbose, offset=offset+2)


class Player():
    def __init__(self, llm, name='Anonymous', kind='ai', persona=None):
        self.llm = llm
        self.name = name
        self.kind = kind
        self.persona = persona

    def respond(self, history=None, query=None, max_tries=64, verbose=0):
        persona = self.persona
        if query is None:
            query = 'What action or actions do you take in response?'

        # Define template and included variables
        template = ''
        variables = {}
        if persona is not None:
            template += 'You are {persona}.\n\n'
            variables['persona'] = persona
        if history is not None:
            template += 'This is what has happened so far.\n{history}\n'
            variables['history'] = history.str(name=self.name)
        template += 'Question:\n"""\n{query}'
        variables['query'] = query
        if persona is not None:
            template += ' (Remember, you are {persona}.)'
        template += '\n"""\nAnswer:\n"""'

        prompt = langchain.prompts.PromptTemplate(
            template=template,
            input_variables=list(variables.keys()),
        )
        llm = self.llm.llm.bind(**self.llm.bound)
        chain = prompt | llm
        if verbose >= 2:
            print(prompt.format(**variables))
            print()
        for i in range(max_tries):
            output = chain.invoke(variables).strip()
            if len(output) > 0:
                break
        print()
        if False:
            prompt_desub = langchain.prompts.PromptTemplate.from_template(
                '{input}')
            chain_desub = desubjunctifier(llm)
            output = chain_desub.invoke(input=output).strip()
        return output

    def synthesize(self, history=None, responses=None, verbose=0):
        persona = self.persona

        # Define template and included variables
        template = ''
        variables = {}
        if persona is not None:
            template += 'You are {persona}.\n\n'
            variables['persona'] = persona
        if history is not None:
            template += 'This is what has happened so far.\n{history}\n'
            variables['history'] = history.str(name=self.name)
        if responses is not None:
            template += 'These are the actions your team members recommend you take in response.\n{responses}\n'
            variables['responses'] = responses.str(name=self.name)
        # template += 'What action or actions do you take in response?:'
        template += 'Combine the recommended actions given above:'
        # if persona is not None:
        #     template += ' (Remember, you are {persona}.)'

        prompt = langchain.prompts.PromptTemplate(
            template=template,
            input_variables=list(variables.keys()),
        )
        chain = prompt | self.llm.llm.bind(**self.llm.bound)
        if verbose >= 2:
            print(prompt.format(**variables))
            print()
        output = chain.invoke(variables).strip()
        print()
        return output

    def info(self, verbose=0, offset=0):
        print(' ' * offset + 'Player:', self.name)
        print(' ' * offset + '  Type:', self.kind)
        print(' ' * offset + '  Persona:', self.persona)


def desubjunctifier(llm):
    examples = [{
        'input': "We would undertake an audit.",
        'output': "We undertake an audit.",
    },{
        'input': "I could send a message to the ambassador, stating our intentions.",
        'output': "I send a message to the ambassador, stating our intentions.",
    },{
        'input': "I implement a response plan.",
        'output': "I implement a response plan.",
    },{
        'input': "After that, immediately deploy the medics.",
        'output': "After that, we immediately deploy the medics.",
    },{
        'input': "Continue to investigate the source of the problems.",
        'output': "I continue to investigate the source of the problems.",
    },{
        'input': "I will refrain from making accusations.",
        'output': "I refrain from making accusations.",
    },{
        'input': "We should optimize the allocation of resources to different departments.",
        'output': "We optimize the allocation of resources to different departments.",
    },{
        'input': "This should not be thrown away.",
        'output': "This is not thrown away.",
    }]
    example_template = 'Input: {input}\nOutput: {output}'
    example_prompt = langchain.prompts.PromptTemplate(
        template=example_template,
        input_variables=['input', 'output']
    )
    # example_selector = langchain.\
    #     prompts.example_selector.LengthBasedExampleSelector(
    #     examples=examples,
    #     example_prompt=example_prompt
    # )
    prefix = 'Replace the subjunctive voice and other indirect speech with present-tense declarative statements.'
    suffix = 'Input: {input}\nOutput: '
    prompt = langchain.prompts.FewShotPromptTemplate(
        examples=examples,
        #example_selector=example_selector,
        example_prompt=example_prompt,
        #example_separator='\n\n',
        prefix=prefix,
        suffix=suffix,
        input_variables=['input']
    )
    print('*****')
    print(prompt.format(input='INPUT_HERE'))
    print('*****')
    chain = {'input': langchain.schema.runnable.RunnablePassthrough()} | prompt | llm
    return chain
