# ComfyUI API submission (experimental)

This is an opt-in local API integration. It requires ComfyUI already running at http://127.0.0.1:8188 and a workflow exported in **API format**, not the regular UI graph JSON.

```bash
python3 -m workshop.cli add image "Generate character sprite" --workflow /absolute/path/to/workflow-api.json
python3 -m workshop.worker --allow-comfyui
python3 -m workshop.cli list
```

**Important:** A returned prompt ID means ComfyUI **accepted** the workflow, not that image generation completed. The queue currently places accepted submissions in manual review; output polling, history lookup and artifact collection are not implemented. Do not approve the task until the output is inspected.

Only localhost HTTP origins are accepted. A ComfyUI workflow may run custom nodes and read/write local files, so use trusted workflows only. There is no model installation, GPU scheduler or unattended execution yet.

To run tests:

```bash
python3 -m unittest discover -s tests -v
```
