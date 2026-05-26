import os
import yaml
from dotenv import load_dotenv

# Load config
config_path = next(
    p for p in ['config.yaml', 'paperflow/config.yaml', '../config.yaml']
    if os.path.exists(p)
)
with open(config_path, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

base_url = config['llm']['providers']['deepseek']['base_url']
model = config['llm']['providers']['deepseek']['models']['cheap']

# Load API Key
env_path = next(
    p for p in ['.env', 'paperflow/.env', '../.env']
    if os.path.exists(p)
)
load_dotenv(env_path)
api_key = os.environ.get('DEEPSEEK_API_KEY')

print(f"Testing LLM Connection: base_url={base_url}, model={model}")
if not api_key:
    print("ERROR: DEEPSEEK_API_KEY not found in .env")
    exit(1)

try:
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Hello, are you there? Just reply 'Yes'."}],
        max_tokens=10
    )
    print("LLM Response:", response.choices[0].message.content)
    print("LLM Test: SUCCESS")
except Exception as e:
    print("LLM Test: FAILED")
    print(e)
    exit(1)
