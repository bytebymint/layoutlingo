#!/usr/bin/env python
"""Test FreeModel API connectivity."""

import json
import os

import requests

API_BASE_URL = 'https://api.freemodel.dev/v1'
API_KEY = os.environ.get('FREEMODEL_API_KEY', '').strip()
MODEL = 'openai-t0'

def test_chat():
    """Test chat completion."""
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    
    payload = {
        'model': MODEL,
        'messages': [
            {'role': 'system', 'content': 'You are a helpful assistant.'},
            {'role': 'user', 'content': 'Say hello'}
        ],
        'temperature': 0.2,
        'max_tokens': 50
    }
    
    try:
        print('Testing FreeModel Chat API...')
        print(f'URL: {API_BASE_URL}/chat/completions')
        
        response = requests.post(
            f'{API_BASE_URL}/chat/completions', 
            headers=headers, 
            json=payload, 
            timeout=30
        )
        print(f'Status Code: {response.status_code}')
        print(f'Response Text: {response.text[:500]}')
        
        if response.status_code == 200:
            result = response.json()
            print('SUCCESS! Response:', result['choices'][0]['message']['content'])
            return True
        else:
            print('FAILED!')
            return False
    except Exception as e:
        print(f'ERROR: {str(e)}')
        import traceback
        traceback.print_exc()
        return False

def test_embedding():
    """Test embedding endpoint."""
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    
    payload = {
        'model': 'text-embedding-3-small',
        'input': 'Hello world'
    }
    
    try:
        print('\nTesting FreeModel Embedding API...')
        print(f'URL: {API_BASE_URL}/embeddings')
        
        response = requests.post(
            f'{API_BASE_URL}/embeddings', 
            headers=headers, 
            json=payload, 
            timeout=30
        )
        print(f'Status Code: {response.status_code}')
        print(f'Response Text: {response.text[:500]}')
        
        if response.status_code == 200:
            result = response.json()
            print('SUCCESS! Embedding dimensions:', len(result['data'][0]['embedding']))
            return True
        else:
            print('FAILED!')
            return False
    except Exception as e:
        print(f'ERROR: {str(e)}')
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    if not API_KEY:
        raise SystemExit('Set FREEMODEL_API_KEY before running this connectivity check.')
    print('=' * 60)
    chat_ok = test_chat()
    print('=' * 60)
    embed_ok = test_embedding()
    print('=' * 60)
    print(f'Chat API: {"✓ WORKING" if chat_ok else "✗ FAILED"}')
    print(f'Embedding API: {"✓ WORKING" if embed_ok else "✗ FAILED"}')
