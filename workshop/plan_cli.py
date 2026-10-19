"""Read-only Ollama planning CLI."""
import argparse
import json
from .planner import plan_local

def main():
    parser = argparse.ArgumentParser(description="Propose a local AI task plan without execution")
    parser.add_argument("request")
    parser.add_argument("--model", required=True, help="Installed Ollama model name")
    args = parser.parse_args()
    print(json.dumps(plan_local(args.request, model=args.model), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
