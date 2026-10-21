"""Run read-only checks: python -m workshop.doctor."""
import json
from .preflight import describe_error
from .adapters import capabilities, ollama_models, comfyui_health

def diagnose():
    result = {"executables": capabilities(), "services": {}}
    for name, fn in (("ollama", ollama_models), ("comfyui", comfyui_health)):
        try:
            value = fn()
            result["services"][name] = {"available": True, "details": value}
        except Exception as error:
            result["services"][name] = {"available": False, "reason": describe_error(error)}
    return result

if __name__ == "__main__":
    print(json.dumps(diagnose(), indent=2, ensure_ascii=False))
