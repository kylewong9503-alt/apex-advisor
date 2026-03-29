import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import LLMInvocationError

logger = logging.getLogger(__name__)


class BaseLLMService(ABC):
    """Base class for LLM service integrations."""
    
    def __init__(self):
        self.timeout = 30.0  # 30 second timeout
        self.max_retries = 3
    
    @abstractmethod
    async def generate_response(
        self, 
        prompt: str, 
        system_message: Optional[str] = None,
        **kwargs
    ) -> str:
        """Generate response from LLM."""
        pass
    
    async def _make_request_with_retry(
        self, 
        url: str, 
        headers: Dict[str, str], 
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Make HTTP request with exponential backoff retry."""
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    return response.json()
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError) as e:
                logger.warning(
                    "LLM HTTP attempt %s/%s failed: %s",
                    attempt + 1,
                    self.max_retries,
                    e,
                )
                if attempt == self.max_retries - 1:
                    raise e
                await asyncio.sleep(2**attempt)
        
        raise Exception("Max retries exceeded")


class OpenAIService(BaseLLMService):
    """OpenAI GPT service integration."""
    
    def __init__(self):
        super().__init__()
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.OPENAI_MODEL
        self.base_url = "https://api.openai.com/v1"
    
    async def generate_response(
        self, 
        prompt: str, 
        system_message: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        """Generate response using OpenAI API."""
        if not self.api_key:
            raise LLMInvocationError(
                "OpenAI API key 未配置",
                recovery_suggestion="设置环境变量 OPENAI_API_KEY 或使用本地 Stub（无密钥时自动启用）。",
            )

        if system_message and (
            system_message.startswith("[profiler:")
            or system_message.startswith("[intelligence_classifier]")
            or system_message.startswith("[deliverable_review]")
            or system_message.startswith("[deliverable_pattern]")
            or system_message.startswith("[followup_extractor]")
            or system_message.startswith("[industry_classifier]")
            or system_message.startswith("[prompt_engine]")
            or system_message.startswith("[chatbox]")
        ):
            parts = system_message.split("\n", 1)
            system_message = parts[1] if len(parts) > 1 else system_message

        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature
        }
        
        if max_tokens:
            payload["max_tokens"] = max_tokens

        try:
            response_data = await self._make_request_with_retry(
                f"{self.base_url}/chat/completions",
                headers,
                payload,
            )
            return response_data["choices"][0]["message"]["content"]
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError, KeyError, IndexError) as e:
            logger.exception("OpenAI chat completion failed after retries")
            raise LLMInvocationError(
                "AI 服务暂时不可用，请稍后重试。",
                recovery_suggestion="检查网络与 API 配额；系统将自动重试最多 3 次。",
                details={"cause": type(e).__name__},
            ) from e


class StubLLMService(BaseLLMService):
    """Deterministic LLM stand-in for tests and dev when no API key is set."""

    async def generate_response(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        sm = system_message or ""
        if sm.startswith("[chatbox]"):
            return json.dumps(
                {
                    "reply": (
                        "Stub：已结合所选档案层与上下文生成回答。"
                        "（连接真实模型后将输出完整内容。）"
                    ),
                    "chain_of_thought": (
                        "1. 解析 SLASH_MODE 与 PROFILE_CONTEXT。\n"
                        "2. 按 CONTEXT_TAGS 过滤可用事实。\n"
                        "3. 组织用户可见结论并标注来源。"
                    ),
                    "sources_used": [
                        {"layer": "team", "summary": "Stub：团队层摘要占位"},
                        {"layer": "company", "summary": "Stub：公司层摘要占位"},
                    ],
                },
                ensure_ascii=False,
            )
        if sm.startswith("[prompt_engine]"):
            return json.dumps(
                {
                    "template": (
                        "Stub Prompt_Template\n\n"
                        "你是一位职场写作助手。请根据用户提供的 Team / Company / Work / Industry "
                        "上下文完成本场景任务，并遵守禁忌约束段落。\n"
                    ),
                    "placeholder_hints": ["Stub：可在档案中补充更完整的公司与行业情报"],
                },
                ensure_ascii=False,
            )
        if sm.startswith("[industry_classifier]"):
            return json.dumps(
                {
                    "industry_trends": "Stub：行业数字化渗透率持续提升。",
                    "competitive_landscape": "Stub：头部厂商份额集中。",
                    "regulatory_dynamics": "Stub：数据出境合规要求趋严。",
                    "data_highlights": "Stub：核心市场规模约 1200 亿元。",
                    "dimension_annotations": {
                        "industry_trends": "supplement",
                        "competitive_landscape": "new",
                        "regulatory_dynamics": "conflict",
                        "data_highlights": "new",
                    },
                    "conflicts": [
                        {
                            "dimension": "regulatory_dynamics",
                            "description": "Stub：与既有“宽松期”判断不一致。",
                        }
                    ],
                    "suggested_merge": {
                        "industry_trends": "supplement",
                        "competitive_landscape": "supplement",
                        "regulatory_dynamics": "supplement",
                        "data_highlights": "override",
                    },
                    "topic_materials": [
                        {
                            "title": "Stub 话题：行业增速与政策边界",
                            "core_insight": "增长与合规需同步评估。",
                            "data_support": "Stub 数据：市场规模 1200 亿。",
                            "tags": ["政策", "增长"],
                        }
                    ],
                    "data_card": {
                        "value": "1200 亿元",
                        "source": "Stub 研报",
                        "as_of": "2026-01-01",
                    },
                },
                ensure_ascii=False,
            )
        if sm.startswith("[followup_extractor]"):
            return json.dumps(
                {
                    "items": [
                        {
                            "description": "Stub：根据会议内容完成 Q1 复盘文档并同步项目组。",
                            "owner": "张三",
                            "due_date": None,
                            "suggested_workstream_name": "产品迭代",
                        },
                        {
                            "description": "Stub：与法务确认合同条款修订截止日。",
                            "owner": None,
                            "due_date": None,
                            "suggested_workstream_name": None,
                        },
                    ]
                },
                ensure_ascii=False,
            )
        if sm.startswith("[deliverable_pattern]"):
            return json.dumps(
                {
                    "persistent_issues": ["格式标题层级一致性"],
                    "improving_dimensions": ["体例与结构"],
                    "compliance_trend": "improving",
                    "trend_description": "Stub：近几次评审显示格式问题在减少，整体合规率上升。",
                },
                ensure_ascii=False,
            )
        if sm.startswith("[deliverable_review]"):
            return json.dumps(
                {
                    "overall_compliance": 0.78,
                    "dimensions": [
                        {
                            "dimension": "format",
                            "status": "compliant",
                            "details": "Stub：结构清晰。",
                            "suggestions": None,
                        },
                        {
                            "dimension": "style",
                            "status": "non-compliant",
                            "details": "Stub：术语不统一。",
                            "suggestions": "统一关键术语表。",
                        },
                        {
                            "dimension": "framework",
                            "status": "compliant",
                            "details": "Stub：论证链完整。",
                            "suggestions": None,
                        },
                        {
                            "dimension": "perspective",
                            "status": "compliant",
                            "details": "Stub：立场明确。",
                            "suggestions": None,
                        },
                        {
                            "dimension": "habit",
                            "status": "compliant",
                            "details": "Stub：符合习惯偏好。",
                            "suggestions": None,
                        },
                    ],
                },
                ensure_ascii=False,
            )
        if sm.startswith("[intelligence_classifier]"):
            return json.dumps(
                {
                    "business_performance": "Stub: Q3 业务增长稳健，毛利率同比改善。",
                    "strategic_direction": "Stub: 战略聚焦云与 AI 转型。",
                    "cultural_signals": "Stub: 强调协作与数据驱动决策。",
                    "internal_rules": "Stub: 跨部门预算需双签。",
                    "conflicts": [
                        {
                            "dimension": "strategic_direction",
                            "description": "Stub: 与既有“降本”叙事需对齐。",
                        }
                    ],
                    "suggested_merge": {
                        "business_performance": "supplement",
                        "strategic_direction": "supplement",
                        "cultural_signals": "supplement",
                        "internal_rules": "override",
                    },
                },
                ensure_ascii=False,
            )
        if sm.startswith("[profiler:finalize]"):
            return json.dumps(
                {
                    "tags": [
                        {
                            "label": "数据驱动",
                            "category": "沟通",
                            "confidence": 0.82,
                        },
                        {
                            "label": "风险规避",
                            "category": "决策",
                            "confidence": 0.65,
                        },
                    ],
                    "update_mode": "increment",
                },
                ensure_ascii=False,
            )
        if sm.startswith("[profiler:chat]"):
            return json.dumps(
                {
                    "reply": "谢谢你的描述。能否举一个他/她在会议或邮件里的具体例子？",
                    "suggested_tags": [],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"reply": "（本地占位）请继续补充。", "suggested_tags": []},
            ensure_ascii=False,
        )


def get_llm_service() -> BaseLLMService:
    """Return OpenAI when configured; otherwise a stub for local dev and CI."""
    key = (settings.OPENAI_API_KEY or "").strip()
    if key:
        return OpenAIService()
    return StubLLMService()