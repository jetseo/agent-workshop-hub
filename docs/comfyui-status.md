# ComfyUI completion tracking

`workshop.comfyui_status.inspect(prompt_id)` checks ComfyUI history for a previously submitted prompt. It reports pending, failed or completed, and returns output image metadata (filename, subfolder, type) when available.

`wait_for_completion(prompt_id, max_wait=300, interval=3)` polls for terminal status, returning timeout if no terminal history appears.

**Worker integration:** The image worker now polls for completion for up to 300 seconds before moving a task into review. A failed or timed-out run is marked failed and never automatically replayed. No image files are downloaded or copied into a workspace. Only local loopback ComfyUI URLs are supported.

The API history response and output metadata should be treated as untrusted data. Do not interpret returned filenames as filesystem paths without validation.
