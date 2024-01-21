#!/usr/bin/env python3

import os
import re
import yaml
import json
import torch
import random
import triton
import typing
import asyncio
import inspect
import numpy as np
import transformers
import langchain.llms
import langchain.chains
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
            self.bound = {'stop': '##'}

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


async def gather_plus(*args):
    items = np.array(args)
    flags = np.array([inspect.isawaitable(x) for x in items])
    if sum(flags) == 0:
        return items
    awaitables  = items[flags]
    outputs = await asyncio.gather(*awaitables)
    items[flags] = outputs
    return items
def make_concrete(args):
    return asyncio.run(gather_plus(*args))


class History():
    def __init__(self):
        self.entries = []
    def __len__(self):
        return len(self.entries)
    def __getitem__(self, index):
        history_part = History()
        if isinstance(index, slice):
            history_part.entries = self.entries[index]
        elif isinstance(index, int):
            history_part.entries = [self.entries[index]]
        return history_part
    def add(self, name, text):
        self.entries.append({'name': name, 'text': text})
    def concrete(self):
        texts = [entry['text'] for entry in self.entries]
        texts = make_concrete(texts)
        for i in range(len(texts)):
            self.entries[i]['text'] = texts[i]
    def str(self, name=None):
        self.concrete()
        return '\n\n'.join(['' \
                        + ('You' if entry['name'] == name else entry['name'])
                        + ':\n\n' + entry['text']
                        for entry in self.entries])
    def textonly(self):
        self.concrete()
        return '\n\n'.join([entry['text'] for entry in self.entries])
    def copy(self):
        history_copy = History()
        history_copy.entries = self.entries.copy()
        return history_copy


class Intelligent():
    def return_output(self, kind=None, bind=None,
                      template=None, variables=None,
                      verbose=1, **kwargs):
        # Set defaults
        if kind is None:
            kind = self.kind

        # Use intelligent entity (AI or human) to generate output
        if template is None and variables is None:
            template, variables = self.return_template(**kwargs)
        prompt = langchain.prompts.PromptTemplate(
            template=template,
            input_variables=list(variables.keys()),
        )
        if kind == 'ai':
            output = self.return_from_ai(prompt, variables, bind=bind,
                                         verbose=verbose)
        elif kind == 'human':
            output = self.return_from_human(prompt, variables, verbose=verbose)
        return output

    def return_template(self, name=None,
                        persona=None, persona_reminder=None,
                        history=None, history_over=None, history_merged=None,
                        responses=None, responses_intro=None,
                        query=None, query_format=None, query_subtitle=None,
                        ):
        # Set defaults
        if name is None:
            pass # Add code to use player name
        if persona is None:
            pass # Add code to use player persona
        if persona is not None:
            if persona[-1:] == '.':
                persona = persona[:-1]

        # Create template and variables
        template = ''
        variables = {}
        if persona is not None:
            template += '### You are {persona}.\n\n'
            variables['persona'] = persona
        if history is not None:
            if not history_over:
                history_intro = 'This is what has happened so far'
            else:
                history_intro = 'This is what happened'
            template += '### ' + history_intro + ':\n\n{history}\n\n'
            if not history_merged:
                variables['history'] = history.str()
            else:
                variables['history'] = history.textonly()
        if responses is not None:
            template += '### ' + responses_intro + ':\n\n{responses}\n\n'
            variables['responses'] = responses.str(name=name)
        if query_format is None or query_format == 'twoline':
            template += '### Question:\n\n{query}'
            if persona is not None and persona_reminder:
                template += ' (Remember, you are {persona}.)'
            template += '\n\n### Answer:\n\n'
        elif query_format == 'oneline':
            template += '### {query}:\n\n'
        elif query_format == 'twoline_simple':
            template += 'Question: {query}\n\nAnswer: '
        elif query_format == 'oneline_simple':
            template += '{query}'
        variables['query'] = query
        if query_subtitle is not None:
            template += '{subtitle}:\n\n'
            variables['subtitle'] = query_subtitle
        return template, variables

    def return_from_ai(self, prompt, variables, max_tries=64, bind=None,
                       verbose=0):
        llm = self.llm.llm.bind(**self.llm.bound)
        if bind is not None:
            llm = llm.bind(**bind)
        chain = prompt | llm
        if verbose >= 2:
            print('v' * 8)
            print(prompt.format(**variables))
            print('^' * 8)
        for i in range(max_tries):
            output = chain.invoke(variables).strip()
            if len(output) > 0:
                break
        print()
        return output

    async def return_from_human(self, prompt, variables, delay=2, verbose=0):
        prompt_path = self.get_iopath(False)
        answer_path = self.get_iopath(True)
        self.human_count += 1

        # Write prompt to disk
        prompt_content = prompt.format(**variables)
        prompt_json = {'content': prompt_content}
        with open(prompt_path, 'w') as f:
            json.dump(prompt_json, f)

        # Read answer from disk
        while not os.path.exists(answer_path):
            await asyncio.sleep(delay)
            if verbose >= 1:
                print('Awaiting %s [ID %i # %i]'
                      % (self.name, self.human_label, self.human_count - 1))
        with open(answer_path, 'r') as f:
            answer_json = json.load(f)
        answer_content = answer_json['content']
        return answer_content

    def set_id(self, verbose=1):
        self.human_label = random.randint(100000, 999999)
        self.human_count = 0
        if verbose >= 1:
            print('ID %i: %s' % (self.human_label, self.name))
        intro_path = self.get_iopath(False)
        intro_json = {'name': self.name, 'persona':
                      self.persona if self.persona is not None else ''}
        with open(intro_path, 'w') as f:
            json.dump(intro_json, f)
        self.human_count += 1

    def get_iopath(self, answer=False, base_path=None):
        if base_path is None:
            base_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), 'messages')
        return os.path.join(base_path, '%i_%i_%s.json'
                            % (self.human_label, self.human_count,
                               'answer' if answer else 'prompt'))


class Control(Intelligent):
    def __init__(self, llm_source_name=None, llm_model_name=None):
        self.llm = LLM(source_name=llm_source_name, model_name=llm_model_name)
        self.name = 'Control'
        self.kind = 'ai'
        self.persona = None
        self.history = History()
        if self.kind == 'human':
            self.set_id()

    def run(self):
        raise Exception('! Override this method in the subclass for your specific scenario.')

    def header(self, title, h=0, width=80):
        print()
        if h == 0:
            print('+-' + '-' * min(len(title), width - 4) + '-+')
            print('| ' + title + ' |')
            print('+-' + '-' * min(len(title), width - 4) + '-+')
        elif h == 1:
            print('-' * min(len(title), width))
            print(title)
            print('-' * min(len(title), width))
        else:
            print(title)

    def record_narration(self, narration):
        self.history.add('Narrator', narration)

    def record_response(self, player_name, player_response):
        self.history.add(player_name, player_response)

    def adjudicate(self, history=None, responses=None, query=None,
                   nature=True, timeframe='week', summarize=False, verbose=0):
        responses_intro = 'These are the plans for each person or group'
        if query is None:
            query = 'Weave these plans into a cohesive narrative of what happens in the next ' + timeframe + '.'
            if random.random() < nature:
                query += ' Include unexpected consequences.'
        output = self.return_output(
            history=history,
            responses=responses, responses_intro=responses_intro,
            query=query, query_format='oneline'
        )
        if summarize:
            print('\n### Summary\n')
            template = 'Give a short summary of the News.\n\n### History:\n\n{history}\n\n### News:\n\n{news}\n\n### Summary of the News:\n\n'
            variables = {'history': history.textonly(), 'news': output}
            output = self.return_output(
                template=template, variables=variables
            )
        return output

    def assess(self, history=None, responses=None, query=None, short=False):
        responses_intro = 'Questions about what happened'
        if responses is None:
            query_format = 'twoline'
        else:
            query_format = 'twoline_simple'
        bind = {'stop': ['\n\n']} if short else None
        output = self.return_output(
            bind=bind,
            history=history, history_over=True,
            responses=responses, responses_intro=responses_intro,
            query=query, query_format=query_format
        )
        return output

    def chat(self, history=None, verbose=1):
        if history is None:
            history = self.history
        chatlog = History()
        nb = 2
        persona = 'the Control (a.k.a. moderator) of a simulated scenario'
        chat_intro = 'This is a conversation about what happened'
        if verbose >= 1:
            instructions = 'Start typing to discuss the simulation, or press Enter twice to exit.'
            self.header(instructions, h=1)
        while True:
            # Get user input, which may be multiline if no line is blank
            usertext = ''
            while True:
                userline = input()
                usertext += userline + '\n'
                if len(usertext) >= nb and usertext[-nb:] == '\n' * nb:
                    break
            if len(usertext) == nb and usertext[-nb:] == '\n' * nb:
                break
            usertext = usertext.strip()
            chatlog.add('User', usertext)

            # Get response
            bind = {'stop': ['User:', 'Control:', 'Narrator:']}
            output = self.return_output(
                bind=bind,
                persona=persona,
                history=history, history_over=True,
                responses=chatlog, responses_intro=chat_intro,
                query='Control:\n\n', query_format='oneline_simple'
            )
            print()
            chatlog.add('Control', output)

    def create_scenario(self, query=None, clip=0):
        if query is None:
            raise Exception('Query required to create scenario.')
        output = self.return_output(
            query=query, query_format='twoline_simple'
        )
        if clip > 0:
            output = '\n\n'.join(output.split('\n\n')[:-clip])
        return output

    def create_players(self, scenario, max_players=None, query=None,
                       npcs=False, pattern_sep=None, pattern_left=None):
        if query is None:
            query = 'List the key players in this scenario, separated by semicolons.'
        if pattern_sep is None:
            pattern_sep = '[\.\,;\n0-9]+'
        if pattern_left is None:
            pattern_left = ' ()-'
        template = 'Scenario: {scenario}\n\nQuestion: {query}\n\nAnswer: '
        variables = {'scenario': scenario, 'query': query}
        output = self.return_output(
            template=template, variables=variables
        )
        names = re.split(pattern_sep, output)
        names = [name.lstrip(pattern_left).rstrip() for name in names]
        names = [name for name in names if len(name) > 0]
        if max_players is None:
            player_names = names
            npc_names = []
        else:
            player_names = names[:max_players]
            npc_names = names[max_players:]
        players = [Player(llm=self.llm, name=name, persona=name)
                   for name in player_names]
        if not npcs:
            return players
        else:
            return players, npc_names

    def create_inject(self, history=None, query=None):
        if query is None:
            raise Exception('Query required to create inject.')
        output = self.return_output(
            history=history,
            query=query, query_format='oneline', query_subtitle='Narrator'
        )
        return output


class Team():
    def __init__(self, name='Anonymous', leader=None, members=None):
        self.name = name
        self.leader = leader
        self.members = members
        self.history = History()

    def respond(self, history=None, query=None, verbose=0):
        member_responses = History()
        for member in self.members:
            if verbose >= 1:
                print('\n### ' + member.name)
            member_responses.add(member.name, member.respond(
                history=history, query=query, verbose=verbose))
        if verbose >= 1:
            print('\n### Leader: ' + self.leader.name)
        leader_response = self.leader.synthesize(
            history=history, responses=member_responses, query=query,
            verbose=verbose)
        return leader_response

    def synthesize(self, history=None, responses=None, query=None, verbose=0):
        return self.leader.synthesize(
            history=history, responses=responses, query=query, verbose=verbose)

    def info(self, verbose=0, offset=0):
        print(' ' * offset + 'Team:', self.name)
        print(' ' * offset + '  Leader:', self.leader.name)
        print(' ' * offset + '  Members:', [member.name
                                            for member in self.members])
        if verbose >= 1:
            for member in self.members:
                member.info(verbose=verbose, offset=offset+2)


class Player(Intelligent):
    def __init__(self, llm=None, name='Anonymous', kind='ai', persona=None):
        self.llm = llm
        self.name = name
        self.kind = kind
        self.persona = persona
        self.history = History()
        if self.kind == 'human':
            self.set_id()

    def respond(self, history=None, query=None, verbose=0):
        if query is None:
            query = 'What action or actions do you take in response?'
        bind = {'stop': ['Narrator:']}
        output = self.return_output(
            bind=bind,
            persona=self.persona, persona_reminder=True,
            history=history,
            query=query
        )
        return output

    def synthesize(self, history=None, responses=None, query=None, verbose=0):
        if query is None:
            responses_intro = 'These are the actions your team members recommend you take in response'
            synthesize_query = 'Combine the recommended actions given above'
        else:
            responses_intro = 'These are the responses from your team members'
            synthesize_query = 'Combine the responses given above'
        output = self.return_output(
            name=self.name,
            persona=self.persona,
            history=history,
            responses=responses, responses_intro=responses_intro,
            query=synthesize_query, query_format='oneline'
        )
        return output

    def info(self, verbose=0, offset=0):
        print(' ' * offset + 'Player:', self.name)
        print(' ' * offset + '  Type:', self.kind)
        print(' ' * offset + '  Persona:', self.persona)
