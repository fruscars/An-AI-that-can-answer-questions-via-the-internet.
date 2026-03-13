import json
import logging
import re
from typing import Optional, Dict, List, Any

from knweb_search import *
from web_search_function import *
from local_qa import *
from zhipuai import ZhipuAI  # 智谱 AI 官方库

# ==================== 配置日志 ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ==================== 工具定义 ====================
DEFAULT_REQUIRED_QUERY_ = [
    {
        "type": "function",
        "function": {
            "name": "find_knowledge_points",
            "description": "根据用户输入查找知识点，返回匹配的知识点名称及类别。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_input": {"type": "string", "description": "用户查询文本，例如'哈夫曼编码'"}
                },
                "required": ["user_input"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_questions_by_knowledge_point",
            "description": "根据知识点名称获取该知识点下的所有题目。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kp_name": {"type": "string", "description": "知识点名称，如'哈夫曼编码'"},
                    "limit": {"type": "integer", "description": "返回题目数量上限，默认10"}
                },
                "required": ["kp_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_questions",
            "description": "在题目内容中搜索包含特定关键词的题目。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "description": "返回题目数量上限，默认10"}
                },
                "required": ["keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_all_knowledge_points",
            "description": "获取数据库中的所有知识点列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回知识点数量上限，默认50"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_similar_knowledge_points",
            "description": "根据用户输入推荐相似的知识点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_input": {"type": "string", "description": "用户查询文本"},
                    "limit": {"type": "integer", "description": "推荐数量上限，默认10"}
                },
                "required": ["user_input"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_question_detail",
            "description": "根据题目ID获取该题目的完整详情（包括答案、解析、关联知识点等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string", "description": "题目ID，如'q_abc123'"}
                },
                "required": ["question_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_chapters",
            "description": "获取所有章节的名称和ID。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回章节数量上限，默认50"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_questions_by_chapter",
            "description": "根据章节名称或章节ID获取该章节下的所有题目。建议优先使用章节ID。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_name": {"type": "string", "description": "章节名称（可选，与chapter_id二选一）"},
                    "chapter_id": {"type": "string", "description": "章节ID（可选，与chapter_name二选一）"},
                    "limit": {"type": "integer", "description": "返回题目数量上限，默认10"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_knowledge_points_by_chapter",
            "description": "根据章节名称或章节ID获取该章节下的所有知识点。建议优先使用章节ID。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_name": {"type": "string", "description": "章节名称（可选）"},
                    "chapter_id": {"type": "string", "description": "章节ID（可选）"},
                    "limit": {"type": "integer", "description": "返回知识点数量上限，默认50"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_questions_by_difficulty",
            "description": "按难度筛选题目，可同时指定知识点名称（可选）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "difficulty": {"type": "string", "description": "难度等级：简单/中等/困难"},
                    "kp_name": {"type": "string", "description": "知识点名称（可选）"},
                    "limit": {"type": "integer", "description": "返回题目数量上限，默认10"}
                },
                "required": ["difficulty"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_knowledge_point_hierarchy",
            "description": "获取知识点的前置知识或后续知识。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kp_name": {"type": "string", "description": "知识点名称"},
                    "direction": {"type": "string", "description": "方向：prerequisite(前置知识)/successor(后续知识)",
                                  "enum": ["prerequisite", "successor"]}
                },
                "required": ["kp_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_questions_by_multiple_kps",
            "description": "查找同时考查多个知识点（交集）或其中任意知识点（并集）的题目。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kp_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "知识点名称列表，例如['二叉树','递归']"
                    },
                    "match_all": {"type": "boolean", "description": "True-同时包含所有知识点，False-包含任一知识点"},
                    "limit": {"type": "integer", "description": "返回题目数量上限，默认10"}
                },
                "required": ["kp_names"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_statistics",
            "description": "获取数据库整体统计信息，包括题目总数、知识点总数、难度分布等。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_hot_knowledge_points",
            "description": "获取题目数量最多的知识点（高频考点）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回数量上限，默认10"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_similar_questions",
            "description": "根据题目ID推荐相似题目（基于共享知识点）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string", "description": "题目ID"},
                    "limit": {"type": "integer", "description": "推荐数量上限，默认5"}
                },
                "required": ["question_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_by_answer_keyword",
            "description": "在题目的答案或答案解析中搜索关键词。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "description": "返回题目数量上限，默认10"}
                },
                "required": ["keyword"]
            }
        }
    },
    # ===== 联网搜索工具 =====
    {
        "type": "function",
        "function": {
            "name": "crawl_by_keyword",
            "description": "联网依据问题关键词爬取csdn的相关信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词"},
                    "max_search_pages": {"type": "integer", "description": "搜索的最大页数（默认2）", "default": 2},
                    "max_articles": {"type": "integer", "description": "最多抓取的文章数量（默认4）", "default": 4}
                },
                "required": ["keyword"]
            }
        }
    },
    # ===== 向量检索工具 =====
    {
        "type": "function",
        "function": {
            "name": "vector_search",
            "description": "向量检索关于计算机相关知识",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索查询内容"},
                    "collection_name": {"type": "string", "description": "向量库集合名，默认为default"}
                },
                "required": ["query"]
            }
        }
    },
    # ===== 历史检索工具 =====
    {
        "type": "function",
        "function": {
            "name": "search_history",
            "description": "检索当前会话的历史消息，找出与用户当前问题相关的内容，用于保持对话连续性。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "当前用户输入的问题或关键词"},
                    "session_id": {"type": "string", "description": "当前会话ID，例如 '20260308_223541'"},
                    "max_results": {"type": "integer", "description": "最多返回几条相关历史消息，默认5"}
                },
                "required": ["query"]
            }
        }
    }
]

# 全局工具列表
QUERY_ = DEFAULT_REQUIRED_QUERY_

# ==================== 初始化智谱 AI 客户端 ====================
from config import  *
API_KEY =CHATBOT_API_KEY  # 请替换为实际有效 key
client = ZhipuAI(api_key=API_KEY)

# ==================== 核心问答类 ====================
class Chat_search:
    def __init__(self, api_key: str, history_data: Optional[Dict[str, List[Dict[str, str]]]] = None):
        self.api_key = api_key
        self.client = client
        self.history_data = history_data or {}          # 所有会话的历史记录
        self.current_session_id: Optional[str] = None   # 当前会话ID
        self.interrupted = False

        # 工具映射
        self.tool_map = {
            "find_knowledge_points": kw.tool_find_knowledge_points,
            "get_questions_by_knowledge_point": kw.tool_get_questions_by_knowledge_point,
            "search_questions": kw.tool_search_questions,
            "get_all_knowledge_points": kw.tool_get_all_knowledge_points,
            "suggest_similar_knowledge_points": kw.tool_suggest_similar_knowledge_points,
            "get_question_detail": kw.tool_get_question_detail,
            "get_chapters": kw.tool_get_chapters,
            "get_questions_by_chapter": kw.tool_get_questions_by_chapter,
            "get_knowledge_points_by_chapter": kw.tool_get_knowledge_points_by_chapter,
            "get_questions_by_difficulty": kw.tool_get_questions_by_difficulty,
            "get_knowledge_point_hierarchy": kw.tool_get_knowledge_point_hierarchy,
            "get_questions_by_multiple_kps": kw.tool_get_questions_by_multiple_kps,
            "get_statistics": kw.tool_get_statistics,
            "get_hot_knowledge_points": kw.tool_get_hot_knowledge_points,
            "get_similar_questions": kw.tool_get_similar_questions,
            "search_by_answer_keyword": kw.tool_search_by_answer_keyword,
            "vector_search": vec.vector_search,
            "crawl_by_keyword": wb.crawl_by_keyword,
            "search_history": self._search_history      # 新增的历史检索工具
        }

        # 工具别名映射（用于纠正模型输出的错误工具名）
        self.tool_aliases = {
            "get_questions": "get_questions_by_chapter",
            "get_knowledge_points": "get_knowledge_points_by_chapter",
            "find_questions": "search_questions",
            "get_chapter_questions": "get_questions_by_chapter",
            "get_chapter_knowledge_points": "get_knowledge_points_by_chapter",
            "query_questions": "search_questions",
        }

    def _search_history(self, query: str, session_id: str = None, max_results: int = 5) -> str:
        """
        检索与 query 相关的历史消息，返回格式化文本。
        使用简单的词重叠度排序，若没有相关则返回最近几条。
        """
        if session_id is None:
            session_id = self.current_session_id
        if not session_id or session_id not in self.history_data:
            return "没有找到该会话的历史记录。"

        messages = self.history_data[session_id]  # 格式为 [{"role": "user"/"assistant", "content": ...}]
        if not messages:
            return "该会话暂无历史消息。"

        # 简单的相关性匹配：计算每条历史消息与 query 的词重叠度
        query_words = set(re.findall(r'\w+', query.lower()))
        scored = []
        for msg in messages:
            content = msg.get('content', '')
            words = set(re.findall(r'\w+', content.lower()))
            overlap = len(query_words & words)
            if overlap > 0:
                scored.append((overlap, msg))

        # 按重叠度降序，取前 max_results 条
        scored.sort(key=lambda x: x[0], reverse=True)
        top_messages = [msg for _, msg in scored[:max_results]]

        if not top_messages:
            # 若无直接相关，返回最近几条消息
            top_messages = messages[-max_results:]

        # 格式化为易读文本
        result_lines = []
        for msg in top_messages:
            role = "用户" if msg['role'] == 'user' else "助手"
            # 可截断过长内容
            content = msg['content'][:200] + "..." if len(msg['content']) > 200 else msg['content']
            result_lines.append(f"[{role}] {content}")
        return "\n".join(result_lines)

    def get_schema(self) -> List[Dict[str, Any]]:
        """返回工具定义列表"""
        return QUERY_

    def process(self,
                user_query: str,
                mode: str = "vector,online",
                session_id: Optional[str] = None,
                history_data: Optional[Dict[str, List[Dict[str, str]]]] = None,
                history_window: int = 2) -> str:
        """
        处理用户查询，支持连续对话。

        :param user_query: 用户输入文本
        :param mode: 模式，包含 "vector" 启用向量检索，包含 "online" 启用联网搜索
        :param session_id: 当前会话ID，用于检索历史
        :param history_data: 可更新的历史数据（若提供则合并）
        :param history_window: 自动注入的最近对话轮数（每轮一问一答两条消息）
        :return: 助手回复
        """
        if session_id:
            self.current_session_id = session_id
        if history_data is not None:
            # 合并历史数据（简单覆盖，可根据需求优化）
            self.history_data.update(history_data)

        # 解析模式
        mode_lower = mode.lower()
        vector_enabled = "vector" in mode_lower
        online_enabled = "online" in mode_lower

        # 处理直接输入的XML工具调用（兼容原逻辑）
        if user_query.strip().startswith('<tool_call>'):
            parsed = kw._parse_xml_tool_call(user_query)
            if parsed:
                tool_name, args = parsed
                logger.info(f"直接执行用户输入的XML工具调用: {tool_name}, 参数: {args}")
                if tool_name in self.tool_map:
                    try:
                        tool_result = self.tool_map[tool_name](**args)
                        if not isinstance(tool_result, str):
                            tool_result = json.dumps(tool_result, ensure_ascii=False, indent=2)
                        return f"✅ 工具执行成功：\n{tool_result}"
                    except Exception as e:
                        return f"❌ 工具执行失败：{str(e)}"
                else:
                    return f"❌ 未知工具名：{tool_name}"

        # 构建系统消息
        system_content = (
            "你是一个数据库查询助手，可以通过调用工具获取信息。"
            "下面是最近的部分历史对话，供你参考。如果需要更早的历史信息，可以调用 search_history 工具。"
            "请根据用户问题调用合适的工具，如果知识库工具获取不到知识，就调用web搜索工具。"
            "如果一次调用不够，可以多次调用，直到收集足够信息。"
            "当信息足够时，用自然语言回答用户。"
        )
        messages = [{"role": "system", "content": system_content}]

        # 注入近期历史
        if self.current_session_id and self.current_session_id in self.history_data:
            hist = self.history_data[self.current_session_id]
            if hist and history_window > 0:
                # 取最近 history_window 轮（每轮2条消息）
                recent = hist[-(history_window * 2):] if len(hist) > history_window * 2 else hist
                for msg in recent:
                    # 只注入 user 和 assistant 消息，避免 tool 消息干扰（可根据需求调整）
                    if msg['role'] in ('user', 'assistant'):
                        messages.append(msg)

        # 添加当前用户问题
        messages.append({"role": "user", "content": user_query})

        # 根据模式过滤可用工具
        all_tools = self.get_schema()
        available_tools = []
        for tool_def in all_tools:
            tool_name = tool_def['function']['name']
            if tool_name == 'vector_search' and not vector_enabled:
                continue
            if tool_name == 'crawl_by_keyword' and not online_enabled:
                continue
            available_tools.append(tool_def)

        max_turns = 10
        turn = 0

        while turn < max_turns:
            turn += 1
            try:
                response = self.client.chat.completions.create(
                    model="glm-4.7",  # 根据实际模型调整
                    messages=messages,
                    tools=available_tools if available_tools else None,
                    tool_choice="auto"
                )
            except Exception as e:
                logger.error(f"模型调用失败: {e}")
                return f"❌ 大模型服务异常：{str(e)}"

            assistant_msg = response.choices[0].message
            messages.append(assistant_msg.model_dump())

            tool_calls = assistant_msg.tool_calls
            logger.info(f"第{turn}轮 tool_calls: {tool_calls}")

            # 备选XML解析（兼容模型返回XML格式）
            if not tool_calls and assistant_msg.content:
                parsed = kw._parse_xml_tool_call(assistant_msg.content)
                if parsed:
                    tool_name, args = parsed
                    logger.info(f"从XML解析到工具调用: {tool_name}, 参数: {args}")
                    if tool_name in self.tool_map:
                        # 构造一个简单的ToolCall对象
                        tool_calls = [
                            type('ToolCall', (), {
                                'id': 'xml_tool_call',
                                'function': type('Function', (), {
                                    'name': tool_name,
                                    'arguments': json.dumps(args, ensure_ascii=False)
                                })
                            })()
                        ]
                    else:
                        logger.warning(f"解析出的工具名 {tool_name} 不在 tool_map 中")

            if tool_calls:
                for tool_call in tool_calls:
                    func_name = self.tool_aliases.get(tool_call.function.name, tool_call.function.name)
                    try:
                        args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        args = {}

                    if func_name in self.tool_map:
                        try:
                            tool_result = self.tool_map[func_name](**args)
                        except Exception as e:
                            tool_result = f"工具执行失败：{str(e)}"
                            logger.error(f"工具 {func_name} 异常: {e}")
                    else:
                        tool_result = f"未知工具：{func_name}，可用工具：{list(self.tool_map.keys())}"

                    # 确保工具结果为字符串
                    if not isinstance(tool_result, str):
                        tool_result = json.dumps(tool_result, ensure_ascii=False, indent=2)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result
                    })
                continue  # 继续循环，让模型根据工具结果生成最终答案
            else:
                # 无工具调用，返回最终回答
                return assistant_msg.content or "（模型未返回有效回答）"

        return "❌ 对话轮次过多，可能陷入循环，请重试。"


# ==================== 实例化 ====================
chat_search = Chat_search(API_KEY)
