#!/usr/bin/env python3
"""Synthetic stdio MCP service proving where the service runs."""
import json
from pathlib import Path
import platform
import sys

for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    method = request.get('method')
    if method == 'initialize':
        result = {'protocolVersion': request['params']['protocolVersion'],
                  'capabilities': {'tools': {}}, 'serverInfo': {'name': 'probe', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'prove_container', 'description': 'Return synthetic container identity.',
                            'inputSchema': {'type': 'object', 'properties': {}}}]}
    elif method == 'tools/call':
        try:
            identity = Path('/tmp/execution-probe-identity').read_text()
            result = {'content': [{'type': 'text', 'text': json.dumps({
                'system': platform.system(), 'container': identity})}]}
        except OSError as e:
            result = {'isError': True, 'content': [{'type': 'text', 'text': json.dumps({
                'system': platform.system(), 'error': str(e)})}]}
    elif method == 'ping':
        result = {}
    else:
        print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'error': {
            'code': -32601, 'message': 'Unsupported fixture method'}}), flush=True)
        continue
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
