# Provider Preset Catalog

The provider wizard contains editable presets derived from provider documentation. API keys are never prefilled. They remain required secrets and are encrypted at rest.

## Direct model providers and coding plans

- **Z.AI:** `https://api.z.ai/api/paas/v4` using the OpenAI-compatible protocol.
- **Z.AI Coding Plan:** `https://api.z.ai/api/coding/paas/v4`. This is a distinct quota endpoint and must use the Coding Plan key.
- **Xiaomi MiMo:** `https://api.xiaomimimo.com/v1` for pay-as-you-go API keys.
- **Xiaomi MiMo Token Plan:** `https://token-plan-cn.xiaomimimo.com/v1` for Token Plan subscription keys. Xiaomi documents pay-as-you-go `sk-` and Token Plan `tp-` credentials as independent.
- **Moonshot Kimi:** `https://api.moonshot.ai/v1`.
- **MiniMax:** `https://api.minimax.io/v1`.

## Open-model inference clouds

- **DeepInfra:** `https://api.deepinfra.com/v1/openai`.
- **Together AI:** `https://api.together.ai/v1`.
- **Fireworks AI:** `https://api.fireworks.ai/inference/v1`.
- **Nebius Token Factory:** `https://api.tokenfactory.nebius.com/v1`.
- **SiliconFlow:** `https://api.siliconflow.com/v1` for the international platform.

## Cloud platforms

- **Alibaba Cloud Model Studio:** `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` is provided as the broadly usable international compatible-mode default. New regional workspaces may have workspace-specific domains; operators can edit the prefilled URL.
- **Volcengine Ark:** `https://ark.cn-beijing.volces.com/api/v3`. Ark also has plan-specific and control-plane endpoints; use the runtime API key that matches this endpoint.

## Source documentation

- Z.AI OpenAI SDK and Coding Plan: <https://docs.z.ai/guides/develop/openai/python> and <https://docs.z.ai/devpack/tool/others>
- Xiaomi MiMo first call and Token Plan overview: <https://mimo.mi.com/docs/en-US/quick-start/summary/first-api-call> and <https://mimo.mi.com/docs/en-US/tokenplan/integration/tools-overview>
- DeepInfra: <https://docs.deepinfra.com/chat/overview>
- Together AI: <https://docs.together.ai/docs/inference/openai-compatibility>
- Fireworks AI: <https://docs.fireworks.ai/tools-sdks/openai-compatibility>
- Nebius Token Factory: <https://docs.tokenfactory.nebius.com/api-reference/introduction>
- SiliconFlow: <https://docs.siliconflow.com/en/userguide/quickstart>
- Moonshot Kimi: <https://platform.kimi.ai/docs/api/quickstart>
- MiniMax: <https://platform.minimax.io/docs/api-reference/text-openai-api>
- Alibaba Cloud Model Studio: <https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope>
- Volcengine Ark: <https://www.volcengine.com/docs/82379/1298459>

## Operational cautions

1. Provider model catalogs and regional endpoints can change. Use **Test & sync models** immediately after saving a connection.
2. A successful model-list request does not prove tools, structured output, embeddings, or streaming. Run the Provider Compatibility Lab before routing production traffic.
3. Do not interchange pay-as-you-go and subscription-plan keys. The wrong endpoint can fail or consume the wrong billing resource.
4. Keep base URLs server-side. The application only allows HTTP(S) endpoints and never returns stored credentials through the product API.
