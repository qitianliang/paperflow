"""Live LLM diagnostic using resolved environment-overridden configuration."""
from paperflow.config import get_config
from paperflow.llm.router import LLMRouter


def main() -> None:
    config = get_config()
    route = config.llm.routing["speed_card"]
    provider = config.llm.providers[route.provider]
    client, model = LLMRouter().get_client_and_model("speed_card")
    print(f"Testing LLM connection: provider={route.provider}, base_url={provider.resolved_base_url}, model={model}")
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply only: OK"}],
        max_tokens=10,
    )
    print("LLM Response:", response.choices[0].message.content)
    print("LLM Test: SUCCESS")


if __name__ == "__main__":
    main()
