# ==================== 提前启动 Neo4j（强制 console 模式） ====================
import subprocess
import atexit
import os
import socket
import time

NEO4J_HOME = r"D:\Nebulagraph\neo4j-community-5.26.20"  # 请根据实际路径修改
NEO4J_BAT = os.path.join(NEO4J_HOME, "bin", "neo4j.bat")
neo4j_process = None

def is_port_open(host, port, timeout=2):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False

def wait_for_neo4j(host='localhost', port=7687, max_attempts=30, delay=2):
    for i in range(max_attempts):
        if is_port_open(host, port):
            print(f"✅ Neo4j 端口 {host}:{port} 已开放，服务就绪。")
            return True
        print(f"⏳ 等待 Neo4j 启动... ({i+1}/{max_attempts})")
        time.sleep(delay)
    print(f"❌ 等待 Neo4j 启动超时，请检查服务状态。")
    return False

def start_neo4j():
    global neo4j_process
    if not os.path.exists(NEO4J_BAT):
        print(f"⚠️ 警告：找不到 neo4j.bat 于 {NEO4J_BAT}，请检查 NEO4J_HOME 配置。")
        return

    if is_port_open('localhost', 7687):
        print("✅ Neo4j 已经在运行中（端口已开放）。")
        return

    print("🚀 直接以 console 模式启动 Neo4j...")
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE  # 隐藏窗口（如需显示改为 SW_SHOW）
        neo4j_process = subprocess.Popen(
            [NEO4J_BAT, "console"],
            shell=True,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        print("🚀 Neo4j console 进程已启动，等待端口开放...")
        if wait_for_neo4j():
            print("✅ Neo4j console 模式启动成功。")
        else:
            print("❌ Neo4j console 模式启动超时。")
    except Exception as e:
        print(f"❌ console 模式启动失败：{e}")

def stop_neo4j():
    global neo4j_process
    if os.path.exists(NEO4J_BAT):
        try:
            subprocess.run([NEO4J_BAT, "stop"], shell=True)
            print("🛑 Neo4j 服务已停止。")
        except Exception as e:
            print(f"服务停止失败：{e}")

    if neo4j_process:
        try:
            neo4j_process.terminate()
            neo4j_process.wait(timeout=5)
            print("🛑 Neo4j console 进程已终止。")
        except Exception as e:
            print(f"终止 console 进程失败：{e}")
        finally:
            neo4j_process = None

atexit.register(stop_neo4j)

# 立即启动 Neo4j（此时还未导入依赖 Neo4j 的模块）
start_neo4j()

# ==================== 导入其他模块（此时 Neo4j 已就绪） ====================
from local_qa import *
import gradio as gr
from search_answer import *
import json
from datetime import datetime
import re
from zhipuai import ZhipuAI  # 确保已安装

# ==================== 全局变量与配置 ====================
collection_name = 'default'
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger()

HISTORY_FILE = "chat_histories.json"

# ==================== 会话管理函数（支持 messages 格式） ====================
def convert_to_messages(old_history):
    """将旧版元组列表转换为 messages 格式"""
    if not old_history:
        return []
    if isinstance(old_history, list) and len(old_history) > 0 and isinstance(old_history[0], tuple):
        new_history = []
        for user, bot in old_history:
            new_history.append({"role": "user", "content": user})
            new_history.append({"role": "assistant", "content": bot})
        return new_history
    return old_history

def load_all_sessions():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            for sid, hist in data.items():
                data[sid] = convert_to_messages(hist)
            return data
    return {}

def save_all_sessions(sessions):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)

def create_new_session():
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    return session_id, []

def get_session_list():
    sessions = load_all_sessions()
    return sorted(sessions.keys(), reverse=True)

def load_session(session_id):
    sessions = load_all_sessions()
    return sessions.get(session_id, [])

def save_session(session_id, messages):
    sessions = load_all_sessions()
    sessions[session_id] = messages
    save_all_sessions(sessions)

def delete_session(session_id):
    sessions = load_all_sessions()
    if session_id in sessions:
        del sessions[session_id]
        save_all_sessions(sessions)

# ==================== 智谱 AI 配置 ====================
API_KEY = "7a4cd2b5736146faa1701773f7c02ca3.3A7T6tWPDooASuxv"  # 请替换为有效 key
client = ZhipuAI(api_key=API_KEY)

# ==================== 工具定义（与之前讨论一致） ====================
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

QUERY_ = DEFAULT_REQUIRED_QUERY_

# ==================== Chat_search 类定义 ====================
class Chat_search:
    def __init__(self, api_key: str, history_data: dict = None):
        self.api_key = api_key
        self.client = client
        self.history_data = history_data or {}          # 所有会话的历史记录
        self.current_session_id = None
        self.interrupted = False

        # 工具映射（假设 kw, vec, wb 已从 local_qa 等导入）
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
            "search_history": self._search_history
        }

        self.tool_aliases = {
            "get_questions": "get_questions_by_chapter",
            "get_knowledge_points": "get_knowledge_points_by_chapter",
            "find_questions": "search_questions",
            "get_chapter_questions": "get_questions_by_chapter",
            "get_chapter_knowledge_points": "get_knowledge_points_by_chapter",
            "query_questions": "search_questions",
        }

    def _search_history(self, query: str, session_id: str = None, max_results: int = 5) -> str:
        """检索与 query 相关的历史消息"""
        if session_id is None:
            session_id = self.current_session_id
        if not session_id or session_id not in self.history_data:
            return "没有找到该会话的历史记录。"
        messages = self.history_data[session_id]
        if not messages:
            return "该会话暂无历史消息。"

        query_words = set(re.findall(r'\w+', query.lower()))
        scored = []
        for msg in messages:
            content = msg.get('content', '')
            words = set(re.findall(r'\w+', content.lower()))
            overlap = len(query_words & words)
            if overlap > 0:
                scored.append((overlap, msg))
        scored.sort(key=lambda x: x[0], reverse=True)
        top_messages = [msg for _, msg in scored[:max_results]]
        if not top_messages:
            top_messages = messages[-max_results:]

        result_lines = []
        for msg in top_messages:
            role = "用户" if msg['role'] == 'user' else "助手"
            content = msg['content'][:200] + "..." if len(msg['content']) > 200 else msg['content']
            result_lines.append(f"[{role}] {content}")
        return "\n".join(result_lines)

    def get_schema(self):
        return QUERY_

    def process(self,
                user_query: str,
                mode: str = "vector,online",
                session_id: str = None,
                history_data: dict = None,
                history_window: int = 2) -> str:
        """处理用户查询，支持连续对话"""
        if session_id:
            self.current_session_id = session_id
        if history_data is not None:
            self.history_data.update(history_data)

        mode_lower = mode.lower()
        vector_enabled = "vector" in mode_lower
        online_enabled = "online" in mode_lower

        system_content = (
            "你是一个数据库查询助手，可以通过调用工具获取信息。"
            "下面是最近的部分历史对话，供你参考。如果需要更早的历史信息，可以调用 search_history 工具。"
            "请根据用户问题调用合适的工具，如果知识库工具获取不到知识，就调用web搜索工具。"
            "当信息足够时，用自然语言回答用户。"
        )
        messages = [{"role": "system", "content": system_content}]

        # 注入近期历史
        if self.current_session_id and self.current_session_id in self.history_data:
            hist = self.history_data[self.current_session_id]
            if hist and history_window > 0:
                recent = hist[-(history_window * 2):] if len(hist) > history_window * 2 else hist
                for msg in recent:
                    if msg['role'] in ('user', 'assistant'):
                        messages.append(msg)

        messages.append({"role": "user", "content": user_query})

        # 根据模式过滤工具
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
                    model="glm-4.7",
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
                parsed = kw._parse_xml_tool_call(assistant_msg.content)  # 假设 kw 中有此函数
                if parsed:
                    tool_name, args = parsed
                    if tool_name in self.tool_map:
                        tool_calls = [
                            type('ToolCall', (), {
                                'id': 'xml_tool_call',
                                'function': type('Function', (), {
                                    'name': tool_name,
                                    'arguments': json.dumps(args, ensure_ascii=False)
                                })
                            })()
                        ]

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
                        tool_result = f"未知工具：{func_name}"

                    if not isinstance(tool_result, str):
                        tool_result = json.dumps(tool_result, ensure_ascii=False, indent=2)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result
                    })
                continue
            else:
                return assistant_msg.content or "（模型未返回有效回答）"

        return "❌ 对话轮次过多，可能陷入循环，请重试。"

# ==================== 实例化 Chat_search ====================
chat_search = Chat_search(API_KEY, history_data=load_all_sessions())

# ==================== 问答函数（支持模式切换和会话ID） ====================
def get_answer(query, history, vector_enabled, online_enabled, session_id):
    """
    根据用户勾选的模式构造 mode 字符串并调用 chat_search.process
    :param query: 用户问题
    :param history: 当前会话的历史消息列表（messages 格式）
    :param vector_enabled: 是否启用向量检索
    :param online_enabled: 是否启用联网搜索
    :param session_id: 当前会话ID
    :return: 助手回复
    """
    mode_parts = []
    if vector_enabled:
        mode_parts.append("vector")
    if online_enabled:
        mode_parts.append("online")
    mode_str = ",".join(mode_parts) if mode_parts else "none"
    try:
        # 将当前会话的历史包装成字典传入，确保 process 内可访问
        history_dict = {session_id: history} if session_id else None
        return chat_search.process(
            query,
            mode_str,
            session_id=session_id,
            history_data=history_dict,
            history_window=2  # 自动注入最近2轮对话
        )
    except Exception as e:
        return f"❌ 处理失败：{str(e)}"

# ==================== 集合管理相关函数 ====================
def get_collection_name(name):
    global collection_name
    collection_name = name

def fetch_ids(collection_name: str) -> gr.CheckboxGroup:
    try:
        result = vec.get_collection_documents(collection_name, None)
        ids = result.get('ids', [])
        logger.info(f"从集合 '{collection_name}' 获取到 {len(ids)} 个ID: {ids}")
        if not ids:
            return gr.CheckboxGroup(choices=["该集合为空"], value=[], label="文档ID", interactive=False)
        return gr.CheckboxGroup(choices=ids, value=[], label=f"选择要删除的文档 (共 {len(ids)} 个)", interactive=True)
    except ValueError as e:
        logger.error(f"集合不存在: {e}")
        return gr.CheckboxGroup(choices=["集合不存在"], value=[], label="错误", interactive=False)
    except Exception as e:
        logger.error(f"获取ID失败: {e}")
        return gr.CheckboxGroup(choices=[f"加载失败: {str(e)}"], value=[], label="错误", interactive=False)

def delete_selected_ids(collection_name, selected_ids: list) -> str:
    if not selected_ids:
        return "请先选择要删除的ID！"
    vec.delete_documents(collection_name, selected_ids)
    return f"成功删除ID：{', '.join(selected_ids)}"

# ==================== 高级暗色主题 CSS ====================
CUSTOM_CSS = """
/* ========== 全局重置 ========== */
.gradio-container {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
    background: #0a0a0f !important;
    min-height: 100vh !important;
    padding: 0 !important;
    margin: 0 !important;
    border: none !important;
    color: #e0e0e0 !important;
}

/* 移除所有默认边框 */
.gradio-container > div,
.gradio-container > div > div,
.gradio-container > div > div > div,
.gradio-container .gr-block,
.gr-box,
.gr-form {
    border: none !important;
    background: transparent !important;
}

/* ========== 整体布局 ========== */
.app-wrapper {
    display: flex !important;
    min-height: 100vh !important;
    background: radial-gradient(circle at 20% 30%, #1a1a2e, #0a0a0f) !important;
}

/* ========== 侧边栏 - 毛玻璃效果 ========== */
.sidebar {
    width: 320px !important;
    background: rgba(20, 20, 30, 0.7) !important;
    backdrop-filter: blur(20px) !important;
    border-right: 1px solid rgba(255, 255, 255, 0.05) !important;
    padding: 0 !important;
    min-height: 100vh !important;
    box-shadow: 5px 0 30px rgba(0, 0, 0, 0.5) !important;
}

.sidebar-content {
    padding: 25px 20px !important;
    height: 100% !important;
}

/* 品牌区域 */
.brand-area {
    margin-bottom: 30px !important;
    padding: 20px !important;
    background: linear-gradient(135deg, rgba(50, 50, 70, 0.3), rgba(30, 30, 50, 0.3)) !important;
    border-radius: 16px !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    box-shadow: 0 8px 20px rgba(0, 0, 0, 0.3) !important;
}
.brand-name {
    font-size: 22px !important;
    font-weight: 700 !important;
    background: linear-gradient(135deg, #a0a0ff, #ffa0a0) !important;
    -webkit-background-clip: text !important;
    -webkit-text-fill-color: transparent !important;
    margin-bottom: 5px !important;
}
.brand-subtitle {
    font-size: 13px !important;
    color: #8888aa !important;
}

/* 可折叠区域 */
.gr-accordion {
    background: rgba(30, 30, 40, 0.4) !important;
    border-radius: 12px !important;
    border: 1px solid rgba(255, 255, 255, 0.05) !important;
    margin-bottom: 15px !important;
    backdrop-filter: blur(10px) !important;
}
.gr-accordion-header {
    background: transparent !important;
    border: none !important;
    color: #ccc !important;
    font-weight: 600 !important;
    padding: 12px 16px !important;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05) !important;
}
.gr-accordion-header:hover {
    background: rgba(255, 255, 255, 0.05) !important;
}
.gr-accordion-content {
    padding: 16px !important;
}

/* 按钮样式 */
.btn {
    border-radius: 10px !important;
    font-weight: 600 !important;
    padding: 10px 18px !important;
    transition: all 0.3s ease !important;
    border: none !important;
    cursor: pointer !important;
    font-size: 14px !important;
    box-shadow: 0 4px 10px rgba(0, 0, 0, 0.3) !important;
}
.btn-primary {
    background: linear-gradient(135deg, #5a5a8a, #3a3a5a) !important;
    color: white !important;
}
.btn-primary:hover {
    background: linear-gradient(135deg, #6a6a9a, #4a4a6a) !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 20px rgba(90, 90, 138, 0.4) !important;
}
.btn-secondary {
    background: rgba(60, 60, 80, 0.5) !important;
    color: #ccc !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    backdrop-filter: blur(5px) !important;
}
.btn-secondary:hover {
    background: rgba(80, 80, 100, 0.6) !important;
    transform: translateY(-2px) !important;
}
.btn-danger {
    background: linear-gradient(135deg, #8a5a5a, #5a3a3a) !important;
    color: #ffb0b0 !important;
}
.btn-danger:hover {
    background: linear-gradient(135deg, #9a6a6a, #6a4a4a) !important;
    transform: translateY(-2px) !important;
}

/* 输入框 */
.input-field, textarea, input[type="text"] {
    background: rgba(30, 30, 40, 0.6) !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    border-radius: 10px !important;
    padding: 12px 16px !important;
    color: #e0e0e0 !important;
    font-size: 14px !important;
    transition: all 0.3s !important;
}
.input-field:focus {
    border-color: #6a6a9a !important;
    box-shadow: 0 0 0 2px rgba(106, 106, 154, 0.3) !important;
    outline: none !important;
}

/* 聊天容器 */
.chat-container {
    background: rgba(20, 20, 30, 0.6) !important;
    backdrop-filter: blur(15px) !important;
    border-radius: 24px !important;
    border: 1px solid rgba(255, 255, 255, 0.05) !important;
    padding: 20px !important;
    box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5) !important;
}
.chat-header {
    padding: 0 0 20px 0 !important;
    border-bottom: 1px solid rgba(255, 255, 255, 0.1) !important;
    margin-bottom: 20px !important;
}
.chat-messages {
    background: rgba(0, 0, 0, 0.2) !important;
    border-radius: 16px !important;
    padding: 16px !important;
    height: 450px !important;
    overflow-y: auto !important;
}

/* 消息气泡（Gradio 会自动应用样式，这里保留自定义可能无用，但保留无妨） */
.user-message, .bot-message {
    max-width: 80% !important;
    margin: 12px 0 !important;
    padding: 14px 18px !important;
    border-radius: 20px !important;
    line-height: 1.5 !important;
    word-wrap: break-word !important;
}
.user-message {
    background: linear-gradient(135deg, #4a4a6a, #3a3a5a) !important;
    color: white !important;
    align-self: flex-end !important;
    border-bottom-right-radius: 4px !important;
}
.bot-message {
    background: rgba(40, 40, 50, 0.8) !important;
    color: #d0d0ff !important;
    border-bottom-left-radius: 4px !important;
    border: 1px solid rgba(255, 255, 255, 0.05) !important;
}

/* 当前集合卡片 */
.current-collection-card {
    background: rgba(30, 30, 40, 0.5) !important;
    backdrop-filter: blur(10px) !important;
    border-radius: 16px !important;
    padding: 20px !important;
    margin-bottom: 20px !important;
    border: 1px solid rgba(255, 255, 255, 0.05) !important;
}
.collection-badge {
    display: inline-flex !important;
    align-items: center !important;
    gap: 8px !important;
    background: rgba(90, 90, 138, 0.2) !important;
    padding: 6px 12px !important;
    border-radius: 30px !important;
    font-size: 13px !important;
    color: #a0a0ff !important;
    border: 1px solid rgba(160, 160, 255, 0.3) !important;
}
.collection-value {
    font-size: 20px !important;
    font-weight: 700 !important;
    margin-top: 8px !important;
    color: white !important;
}

/* 页脚 */
.footer {
    margin-top: 30px !important;
    padding: 20px !important;
    text-align: center !important;
    color: #666688 !important;
    font-size: 13px !important;
    border-top: 1px solid rgba(255, 255, 255, 0.05) !important;
}
.footer-brand {
    font-weight: 600 !important;
    color: #8888aa !important;
}

/* 滚动条 */
::-webkit-scrollbar {
    width: 8px !important;
}
::-webkit-scrollbar-track {
    background: transparent !important;
}
::-webkit-scrollbar-thumb {
    background: #3a3a5a !important;
    border-radius: 4px !important;
}
::-webkit-scrollbar-thumb:hover {
    background: #4a4a6a !important;
}
"""

# ==================== Gradio 界面构建 ====================
with gr.Blocks(title="知识库智能问答系统") as demo:
    # 状态存储
    current_session_id = gr.State(value="")
    chat_history_state = gr.State(value=[])
    sidebar_visible = gr.State(value=True)  # 侧边栏是否可见

    # ============== 主布局 ==============
    gr.HTML("""<style>body,html{margin:0!important;padding:0!important;background:#0a0a0f!important;}</style>""")

    with gr.Row(elem_classes="app-wrapper"):
        # ============== 侧边栏（可折叠） ==============
        with gr.Column(scale=0, visible=True, elem_classes="sidebar") as sidebar_col:
            with gr.Column(elem_classes="sidebar-content"):
                # 折叠按钮（位于侧边栏顶部）
                toggle_sidebar_btn = gr.Button("◀ 折叠侧边栏", elem_classes="btn btn-secondary btn-sm")

                # 品牌区域（始终显示）
                gr.HTML("""<div class="brand-area"><div class="brand-name">知识库系统</div><div class="brand-subtitle">基于 Qwen3-14B & 向量数据库</div></div>""")

                # 系统状态（可折叠）
                with gr.Accordion("系统状态", open=False, elem_classes="sidebar-section"):
                    status_display = gr.Textbox(
                        value="🟢 系统运行正常\n📊 内存使用: 65%\n🔥 GPU负载: 42%\n💾 存储空间: 1.2GB/5GB",
                        interactive=False, lines=5, elem_classes="textarea-field", show_label=False
                    )

                # 集合管理（可折叠）
                with gr.Accordion("集合管理", open=False, elem_classes="sidebar-section"):
                    gr.HTML('<div class="sidebar-header">集合管理</div>')
                    list_all_collection = gr.Textbox(
                        label="所有集合", lines=4, interactive=False,
                        elem_classes="textarea-field", show_label=False, placeholder="暂无集合..."
                    )
                    refresh_btn = gr.Button("🔄 刷新集合", elem_classes="btn btn-secondary btn-sm")
                    collection_name1 = gr.Textbox(placeholder="新集合名称", elem_classes="input-field", show_label=False)
                    create_btn = gr.Button("📝 创建集合", elem_classes="btn btn-primary btn-sm")
                    collection_name2 = gr.Textbox(placeholder="要删除的集合", elem_classes="input-field", show_label=False)
                    delete_btn = gr.Button("🗑️ 删除集合", elem_classes="btn btn-danger btn-sm")

                # 会话管理（可折叠）
                with gr.Accordion("会话管理", open=False, elem_classes="sidebar-section"):
                    gr.HTML('<div class="sidebar-header">会话管理</div>')
                    new_chat_btn = gr.Button("➕ 新建会话", elem_classes="btn btn-primary btn-sm")
                    session_dropdown = gr.Dropdown(
                        label="历史会话", choices=[], value=None, interactive=True, elem_classes="input-field"
                    )
                    load_session_btn = gr.Button("📂 加载选中会话", elem_classes="btn btn-secondary btn-sm")
                    delete_session_btn = gr.Button("🗑️ 删除选中会话", elem_classes="btn btn-danger btn-sm")

                # 文档上传（可折叠）
                with gr.Accordion("文档上传", open=False, elem_classes="sidebar-section"):
                    gr.HTML('<div class="sidebar-header">文档上传</div>')
                    collection_name3 = gr.Textbox(
                        label="目标集合", value="default",
                        placeholder="输入集合名称", elem_classes="input-field"
                    )
                    add_folder = gr.File(
                        label="选择文件",
                        file_count="multiple",
                        file_types=[".txt", ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".md", ".csv", ".json"],
                        elem_classes="upload-area"
                    )
                    add_folder_button = gr.Button("🚀 开始上传并构建知识库", elem_classes="btn btn-primary", size="lg")
                    the_result = gr.Textbox(label="处理结果", lines=4, interactive=False, elem_classes="textarea-field", show_label=False)

                # 文件管理（可折叠）
                with gr.Accordion("文件管理", open=False, elem_classes="sidebar-section"):
                    gr.HTML('<div class="sidebar-header">文件管理</div>')
                    collection_name4 = gr.Textbox(
                        label="集合名称", placeholder="输入要管理的集合名", elem_classes="input-field"
                    )
                    get_ids_btn = gr.Button("📋 获取文档列表", elem_classes="btn btn-primary")
                    delete_id = gr.CheckboxGroup(
                        label="选择要删除的文档", choices=[], elem_classes="input-field"
                    )
                    with gr.Row():
                        delete_ids_btn = gr.Button("🗑️ 删除选中文档", elem_classes="btn btn-danger")
                        refresh_files_btn = gr.Button("🔄 刷新列表", elem_classes="btn btn-secondary")
                    result = gr.Textbox(label="操作结果", interactive=False, elem_classes="input-field")

                # 使用指南（可折叠）
                with gr.Accordion("使用指南", open=False, elem_classes="sidebar-section"):
                    gr.Markdown("""
                    **📦 集合管理**  
                    1. 创建集合 - 在侧边栏输入新集合名称并点击"创建集合"  
                    2. 删除集合 - 输入要删除的集合名并点击"删除集合"  
                    3. 切换集合 - 在主区域顶部输入框输入集合名并点击"确认切换"  

                    **💬 智能对话**  
                    • 确保已选择正确的知识库集合  
                    • 在聊天框输入问题开始对话  
                    • 支持多轮对话和上下文理解  
                    • 点击"清空对话"开始新对话  

                    **📤 文档上传**  
                    1. 在"文档上传"区域选择目标集合  
                    2. 拖拽或选择要上传的文件  
                    3. 点击"开始上传并构建知识库"  

                    **⚠️ 注意事项**  
                    • 确保上传文件格式正确  
                    • 大文件上传需要较长时间  
                    • 删除操作不可逆，请谨慎操作  
                    """)

        # ============== 主内容区（聊天界面） ==============
        with gr.Column(scale=1, elem_classes="main-content"):
            # 当侧边栏折叠时显示的展开按钮（初始隐藏）
            with gr.Row(visible=False) as expand_row:
                expand_btn = gr.Button("▶ 展开侧边栏", elem_classes="btn btn-secondary btn-sm")

            with gr.Column(elem_classes="main-scroll"):
                # 当前集合显示
                current_collection_html = gr.HTML("""
                <div>
                    <div class="collection-badge"><span class="status-indicator"></span>当前使用集合</div>
                    <div class="collection-value" id="current-collection-name">default</div>
                </div>
                """, visible=True)

                # 集合切换卡片
                with gr.Column(elem_classes="card"):
                    gr.HTML("""<div class="card-header"><h3 class="card-title">切换知识库集合</h3><div class="card-subtitle">选择要使用的知识库进行问答</div></div>""")
                    with gr.Row():
                        with gr.Column(scale=3):
                            name = gr.Textbox(value="default", placeholder="输入集合名称", elem_classes="input-field", container=False)
                        with gr.Column(scale=1):
                            name_button = gr.Button("确认切换", elem_classes="btn btn-primary")

                # 聊天界面
                with gr.Column(elem_classes="chat-container"):
                    gr.HTML("""
                    <div class="chat-header">
                        <div style="display: flex; align-items: center; justify-content: space-between;">
                            <div style="display: flex; align-items: center;">
                                <span style="font-size: 18px; font-weight: 600; color: #fff; margin-right: 12px;">智能对话助手</span>
                                <span class="status-indicator" style="background:#4caf50; width:10px; height:10px; border-radius:50%; display:inline-block; margin-right:5px;"></span>
                                <span style="color: #4caf50; font-size: 13px;">在线</span>
                            </div>
                            <div style="font-size: 13px; color: #8888aa;">基于当前知识库实时回答</div>
                        </div>
                    </div>
                    """)
                    chatbot = gr.Chatbot(label="对话记录", height=400, elem_classes="chat-messages")

                    # 模式选择（双复选框）
                    with gr.Row():
                        vector_checkbox = gr.Checkbox(label="向量检索", value=True, elem_classes="input-field", scale=1)
                        online_checkbox = gr.Checkbox(label="联网搜索", value=False, elem_classes="input-field", scale=1)
                    with gr.Row():
                        msg = gr.Textbox(
                            label="输入消息",
                            placeholder="请输入您的问题...（支持多轮对话和上下文理解）",
                            scale=9, elem_classes="input-field", container=False
                        )
                        send_btn = gr.Button("🚀 发送", elem_classes="btn btn-primary", scale=1)
                    with gr.Row():
                        clear_btn = gr.Button("🗑️ 清空当前对话", elem_classes="btn btn-secondary")
                        stop_btn = gr.Button("⏹️ 停止生成", elem_classes="btn btn-secondary")

                # 页脚
                gr.HTML("""<div class="footer"><div class="footer-brand">知识库智能问答系统 v2.5</div><div class="footer-copyright">© 2026 基于 Qwen3-14 & 向量数据库技术 | 现代化暗色主题</div></div>""")

    # ==================== 事件绑定 ====================
    # 侧边栏折叠/展开功能
    def toggle_sidebar(current_visible):
        new_visible = not current_visible
        sidebar_update = gr.update(visible=new_visible)
        expand_row_update = gr.update(visible=not new_visible)
        btn_text = "◀ 折叠侧边栏" if new_visible else "▶ 展开侧边栏"
        btn_update = gr.update(value=btn_text, visible=new_visible)
        return new_visible, sidebar_update, expand_row_update, btn_update

    toggle_sidebar_btn.click(
        fn=toggle_sidebar,
        inputs=[sidebar_visible],
        outputs=[sidebar_visible, sidebar_col, expand_row, toggle_sidebar_btn]
    )

    expand_btn.click(
        fn=toggle_sidebar,
        inputs=[sidebar_visible],
        outputs=[sidebar_visible, sidebar_col, expand_row, toggle_sidebar_btn]
    )

    # 集合管理
    refresh_btn.click(fn=list_collection, outputs=list_all_collection)
    create_btn.click(fn=create_collections, inputs=[collection_name1], outputs=list_all_collection)
    delete_btn.click(fn=delete_collection, inputs=[collection_name2])

    def update_collection_name(name):
        get_collection_name(name)
        return f"""
        <div>
            <div class="collection-badge"><span class="status-indicator"></span>当前使用集合</div>
            <div class="collection-value">{name}</div>
        </div>
        """
    name_button.click(fn=update_collection_name, inputs=name, outputs=current_collection_html)

    # 上传文件
    add_folder_button.click(fn=add_file_to_collection, inputs=[add_folder, collection_name3], outputs=the_result)

    # 文件管理
    get_ids_btn.click(fn=fetch_ids, inputs=collection_name4, outputs=delete_id)
    delete_ids_btn.click(fn=delete_selected_ids, inputs=[collection_name4, delete_id], outputs=result)

    # 会话管理（同步内存历史）
    def create_new_session_fn():
        session_id, messages = create_new_session()
        save_session(session_id, messages)
        # 同步到内存
        chat_search.history_data[session_id] = messages
        return session_id, messages, gr.Dropdown(choices=get_session_list(), value=session_id)

    def load_selected_session(session_id):
        if not session_id:
            return [], []
        messages = load_session(session_id)
        # 同步到内存
        chat_search.history_data[session_id] = messages
        return messages, messages

    def delete_selected_session(session_id):
        if session_id:
            delete_session(session_id)
            # 从内存移除
            if session_id in chat_search.history_data:
                del chat_search.history_data[session_id]
        new_choices = get_session_list()
        new_value = new_choices[0] if new_choices else None
        if new_value:
            messages = load_session(new_value)
        else:
            messages = []
        return gr.Dropdown(choices=new_choices, value=new_value), messages, messages

    new_chat_btn.click(
        fn=create_new_session_fn,
        outputs=[current_session_id, chat_history_state, session_dropdown]
    ).then(fn=lambda: ([], []), outputs=[chatbot, chat_history_state])

    load_session_btn.click(
        fn=load_selected_session,
        inputs=[session_dropdown],
        outputs=[chatbot, chat_history_state]
    ).then(fn=lambda sid: sid, inputs=[session_dropdown], outputs=[current_session_id])

    delete_session_btn.click(
        fn=delete_selected_session,
        inputs=[session_dropdown],
        outputs=[session_dropdown, chatbot, chat_history_state]
    )

    demo.load(
        fn=lambda: (gr.Dropdown(choices=get_session_list()), "", []),
        outputs=[session_dropdown, current_session_id, chat_history_state]
    )

    # ==================== 聊天发送（修复为 messages 格式） ====================
    def user_message_submit(message, history, vector_enabled, online_enabled, session_id):
        if not message.strip():
            return history, history, ""
        new_history = history.copy()  # history 是 messages 格式
        bot_msg = get_answer(message, new_history, vector_enabled, online_enabled, session_id)
        new_history.append({"role": "user", "content": message})
        new_history.append({"role": "assistant", "content": bot_msg})
        if session_id:
            save_session(session_id, new_history)
            # 同步更新内存历史
            chat_search.history_data[session_id] = new_history
        return new_history, new_history, ""

    send_btn.click(
        fn=user_message_submit,
        inputs=[msg, chat_history_state, vector_checkbox, online_checkbox, current_session_id],
        outputs=[chatbot, chat_history_state, msg]
    )
    msg.submit(
        fn=user_message_submit,
        inputs=[msg, chat_history_state, vector_checkbox, online_checkbox, current_session_id],
        outputs=[chatbot, chat_history_state, msg]
    )

    def clear_current():
        return [], []
    clear_btn.click(fn=clear_current, outputs=[chatbot, chat_history_state])

    stop_btn.click(fn=lambda: None)

# ==================== 启动应用 ====================
if __name__ == "__main__":
    demo.queue()
    demo.launch(
        share=False,
        favicon_path=None,
        show_error=True,
        debug=False,
        theme=gr.themes.Soft(primary_hue="stone", secondary_hue="gray", neutral_hue="stone"),
        css=CUSTOM_CSS
    )