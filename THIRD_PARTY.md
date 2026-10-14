# Software attribution and scope

This is an integration and infrastructure reference derived from an existing private deployment. It does not claim authorship of the document management system, inference engines, GPU hardware, or model training.

| Component | Role | Upstream |
| --- | --- | --- |
| Paperless-ngx (observed 3.3.1) | Document management and AI workflow client; GPL-3.0 | https://github.com/paperless-ngx/paperless-ngx |
| Ollama | Local inference API; MIT | https://github.com/ollama/ollama |
| HAProxy 3.2 | HTTP routing and adapter liveness checks; upstream licensing applies | https://www.haproxy.org/ |
| Python standard library | Adapter HTTP server/client, TLS, JSON, threads | https://www.python.org/ |
| Docker / Compose | Deployment tooling | https://docs.docker.com/compose/ |
| OpenAI API | External paid inference service; service terms apply | https://developers.openai.com/api/reference/resources/chat |
| Qwen model family | Local configured model name; model-specific license applies | https://github.com/QwenLM/Qwen3 |

The production adapter's author/license history was not established by the read-only inventory. No new open-source license is asserted for that source. Review ownership/licensing before publishing with an open-source license. Paperless workflow overrides and its classification template are deliberately excluded: they include upstream application code and deployment-specific business rules. This repository contains no third-party model weights or Paperless source distribution.
