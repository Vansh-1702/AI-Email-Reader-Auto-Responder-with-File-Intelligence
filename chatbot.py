import os

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

API_KEY = os.getenv("GROQ_API_KEY")
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

if not API_KEY:
    raise SystemExit("Missing GROQ_API_KEY. Configure it in your .env file.")

client = Groq(api_key=API_KEY)

messages = [
    {
        "role": "system",
        "content": "You are a helpful, accurate, and friendly AI assistant.",
    }
]

MAX_HISTORY_MESSAGES = 20


def get_reply(user_input):
    """Generate a response while keeping conversation context."""
    messages.append({"role": "user", "content": user_input})

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.7,
            max_completion_tokens=1024,
        )

        reply = response.choices[0].message.content or ""
        messages.append({"role": "assistant", "content": reply})

        # Keep the system prompt and recent conversation only.
        if len(messages) > MAX_HISTORY_MESSAGES + 1:
            del messages[1:1 + (len(messages) - MAX_HISTORY_MESSAGES - 1)]

        return reply.strip()

    except Exception:
        messages.pop()  # Remove the unanswered user message.
        raise


def main():
    print(f"AI Chatbot using Groq ({MODEL})")
    print("Type 'exit' or 'quit' to stop.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if user_input.lower() in {"exit", "quit"}:
            print("Goodbye!")
            break

        if not user_input:
            continue

        try:
            reply = get_reply(user_input)
            print(f"\nAssistant: {reply}\n")
        except Exception as exc:
            print(f"\nCould not generate a response: {exc}\n")


if __name__ == "__main__":
    main()