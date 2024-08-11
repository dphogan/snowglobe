#!/usr/bin/env python3

#   Copyright 2024 IQT Labs LLC
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

def config():
    import snowglobe
    snowglobe.config()

def server(host='0.0.0.0', port=8000, log_level='warning'):
    import uvicorn
    uvicorn.run('snowglobe.api:app', host=host, port=port, log_level=log_level)
