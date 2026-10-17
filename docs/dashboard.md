# Local task dashboard

Run `python3 -m workshop.dashboard` from the repository root, then open http://127.0.0.1:8787.

The prototype allows task registration, task status viewing, and manual approval. It binds to loopback only and uses a random form token. It does not launch coding agents, schedule jobs or generate images automatically.

Do not expose the dashboard to the internet. It lacks production authentication and access controls. Avoid including secrets in task metadata.

Run the test suite with `python3 -m unittest discover -s tests -v`.
