import os
import json
import re
import logging
from typing import List, Dict, Any, Optional
from neo4j import GraphDatabase
from datetime import datetime
from dataclasses import dataclass
import time
from zhipuai import ZhipuAI
from config import *
# ==================== 配置区域 ====================

ZHIPU_API_KEY =KNWEB_API_KEY  # 请确保有效
# =================================================

client = ZhipuAI(api_key=ZHIPU_API_KEY)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# -------------------- 数据模型 --------------------
@dataclass
class Question:
    question_id: str
    content: str
    type: str
    difficulty: str
    answer: str
    answer_explanation: str
    question_number: str
    options: Dict[str, str]
    knowledge_points: List[str]
    chapter_name: str = ""

# -------------------- Neo4j检索器（与导入器完全适配）--------------------
class IntelligentNeo4jRetriever:
    def __init__(self, uri: str, username: str, password: str, database: str = "neo4j"):
        try:
            self.driver = GraphDatabase.driver(uri, auth=(username, password), connection_timeout=30)
            self.database = database
            self._test_connection()
            logger.info("✅ Neo4j 连接成功")
        except Exception as e:
            logger.error(f"❌ Neo4j 连接失败: {e}")
            raise

    def _test_connection(self):
        with self.driver.session(database=self.database) as session:
            result = session.run("RETURN 1 AS test")
            if not result.single()["test"] == 1:
                raise Exception("连接测试失败")

    def close(self):
        if self.driver:
            self.driver.close()
            logger.info("Neo4j 连接已关闭")

    # ---------- 清理用户输入 ----------
    def _clean_user_input(self, text: str) -> str:
        stop_words = ["知识点", "知识", "点", "为", "的", "关于", "显示", "查找", "查询",
                      "搜索", "题目", "习题", "试题", "问题", "练习", "所有", "全部"]
        patterns = [
            r'(.*?)的(?:题目|习题|试题|问题|练习)',
            r'关于(.*?)',
            r'显示(.*?)',
            r'查找(.*?)',
            r'查询(.*?)',
            r'搜索(.*?)',
            r'(.*?)知识点'
        ]
        cleaned = text
        for pattern in patterns:
            match = re.search(pattern, text.lower())
            if match:
                extracted = match.group(1).strip()
                if extracted:
                    cleaned = extracted
                    break
        for word in stop_words:
            cleaned = cleaned.replace(word, "")
        cleaned = re.sub(r'[，。；：、？！,.，;:?!"\'\s]+', '', cleaned)
        return cleaned.strip()

    # ---------- 知识点查询 ----------
    def find_knowledge_point_smart(self, user_input: str) -> List[Dict[str, Any]]:
        cleaned = self._clean_user_input(user_input)
        if not cleaned:
            return []
        with self.driver.session(database=self.database) as session:
            strategies = [
                ("MATCH (kp:KnowledgePoint) WHERE kp.name = $name RETURN kp.knowledge_point_id as id, kp.name as name, kp.category as category", cleaned),
                ("MATCH (kp:KnowledgePoint) WHERE kp.name CONTAINS $name RETURN kp.knowledge_point_id as id, kp.name as name, kp.category as category", cleaned),
                ("MATCH (kp:KnowledgePoint) WHERE any(word in split($name, ' ') WHERE kp.name CONTAINS word) RETURN kp.knowledge_point_id as id, kp.name as name, kp.category as category", cleaned),
                ("MATCH (kp:KnowledgePoint) WHERE kp.category CONTAINS $name RETURN kp.knowledge_point_id as id, kp.name as name, kp.category as category", cleaned),
            ]
            results = []
            seen_ids = set()
            for query_template, term in strategies:
                if not term:
                    continue
                try:
                    result = session.run(query_template, {"name": term})
                    for record in result:
                        pid = record["id"]
                        if pid and pid not in seen_ids:
                            seen_ids.add(pid)
                            results.append({
                                "id": pid,
                                "name": record["name"],
                                "category": record.get("category", "数据结构")
                            })
                except Exception as e:
                    logger.debug(f"策略失败: {e}")
            return results

    def get_all_knowledge_points(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self.driver.session(database=self.database) as session:
            query = """
            MATCH (kp:KnowledgePoint)
            OPTIONAL MATCH (q:Question)-[:TESTS_KNOWLEDGE]->(kp)
            WITH kp, count(q) as question_count
            RETURN kp.knowledge_point_id as id, kp.name as name, kp.category as category, question_count
            ORDER BY question_count DESC LIMIT $limit
            """
            result = session.run(query, {"limit": limit})
            return [dict(record) for record in result]

    def get_knowledge_point_info(self, kp_id: str) -> Optional[Dict[str, Any]]:
        with self.driver.session(database=self.database) as session:
            query = """
            MATCH (kp:KnowledgePoint {knowledge_point_id: $kp_id})
            OPTIONAL MATCH (q:Question)-[:TESTS_KNOWLEDGE]->(kp)
            RETURN kp.knowledge_point_id as id, kp.name as name, kp.category as category, count(q) as question_count
            """
            result = session.run(query, {"kp_id": kp_id})
            record = result.single()
            return dict(record) if record else None

    def suggest_similar_knowledge_points(self, user_input: str, limit: int = 10) -> List[Dict[str, Any]]:
        cleaned = self._clean_user_input(user_input)
        if not cleaned:
            return []
        all_kps = self.get_all_knowledge_points(limit=500)
        suggestions = []
        for kp in all_kps:
            kp_name = kp["name"].lower()
            score = 0
            if kp_name == cleaned.lower():
                score += 100
            elif cleaned.lower() in kp_name:
                score += 50
            for word in cleaned.split():
                if word.lower() in kp_name:
                    score += 20
            if kp.get("category") and cleaned.lower() in kp["category"].lower():
                score += 10
            if score > 0:
                kp["match_score"] = score
                suggestions.append(kp)
        suggestions.sort(key=lambda x: x["match_score"], reverse=True)
        return suggestions[:limit]

    # ---------- 题目查询 ----------
    def _record_to_question(self, record) -> Question:
        """将 Neo4j 记录转换为 Question 对象，使用统一的字段名"""
        try:
            # 使用 get 方法避免 KeyError，并提供默认值
            question_id = record.get("question_id", "")
            content = record.get("content", "")
            q_type = record.get("type", "简答题")
            difficulty = record.get("difficulty", "中等")
            answer = record.get("answer", "")
            answer_explanation = record.get("answer_explanation", "")
            question_number = record.get("question_number", "")
            options_json = record.get("options_json", "{}")
            chapter_name = record.get("chapter_name", "")

            try:
                options = json.loads(options_json) if options_json else {}
            except:
                options = {}

            return Question(
                question_id=question_id,
                content=content,
                type=q_type,
                difficulty=difficulty,
                answer=answer,
                answer_explanation=answer_explanation,
                question_number=question_number,
                options=options,
                knowledge_points=[],
                chapter_name=chapter_name
            )
        except Exception as e:
            logger.error(f"转换题目记录失败: {e}, 记录: {record}")
            # 返回一个默认的 Question 对象，避免中断
            return Question(
                question_id="unknown",
                content="题目内容解析失败",
                type="未知",
                difficulty="未知",
                answer="",
                answer_explanation="",
                question_number="",
                options={},
                knowledge_points=[],
                chapter_name=""
            )

    def get_questions_by_knowledge_point_id(self, kp_id: str, limit: int = 50) -> List[Question]:
        with self.driver.session(database=self.database) as session:
            query = """
            MATCH (kp:KnowledgePoint {knowledge_point_id: $kp_id})<-[:TESTS_KNOWLEDGE]-(q:Question)
            OPTIONAL MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q)
            RETURN q.question_id as question_id, q.content as content, q.type as type,
                   q.difficulty as difficulty, q.answer as answer, q.answer_explanation as answer_explanation,
                   q.question_number as question_number, q.options as options_json, c.name as chapter_name
            ORDER BY q.question_number LIMIT $limit
            """
            result = session.run(query, {"kp_id": kp_id, "limit": limit})
            questions = []
            for record in result:
                questions.append(self._record_to_question(record))
            return questions

    def search_questions_in_content(self, keyword: str, limit: int = 50) -> List[Question]:
        with self.driver.session(database=self.database) as session:
            pattern = f".*{re.escape(keyword)}.*"
            query = """
            MATCH (q:Question)
            WHERE q.content =~ $pattern
            OPTIONAL MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q)
            RETURN q.question_id as question_id, q.content as content, q.type as type,
                   q.difficulty as difficulty, q.answer as answer, q.answer_explanation as answer_explanation,
                   q.question_number as question_number, q.options as options_json, c.name as chapter_name
            LIMIT $limit
            """
            result = session.run(query, {"pattern": pattern, "limit": limit})
            questions = []
            for record in result:
                questions.append(self._record_to_question(record))
            return questions

    def get_question_detail(self, question_id: str) -> Optional[Dict[str, Any]]:
        with self.driver.session(database=self.database) as session:
            query = """
            MATCH (q:Question {question_id: $qid})
            OPTIONAL MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q)
            OPTIONAL MATCH (q)-[:TESTS_KNOWLEDGE]->(kp:KnowledgePoint)
            RETURN q, c.name as chapter_name, collect(DISTINCT kp.name) as kp_names
            """
            result = session.run(query, {"qid": question_id})
            record = result.single()
            if not record:
                return None
            q_node = record["q"]
            detail = {
                "question_id": q_node.get("question_id"),
                "content": q_node.get("content"),
                "type": q_node.get("type"),
                "difficulty": q_node.get("difficulty"),
                "answer": q_node.get("answer"),
                "answer_explanation": q_node.get("answer_explanation"),
                "question_number": q_node.get("question_number"),
                "options": json.loads(q_node.get("options", "{}")),
                "chapter_name": record.get("chapter_name", ""),
                "knowledge_points": record.get("kp_names", [])
            }
            return detail

    # ---------- 章节相关（适配导入器）----------
    def get_chapters(self, limit: int = 50) -> List[Dict[str, str]]:
        with self.driver.session(database=self.database) as session:
            query = "MATCH (c:Chapter) RETURN c.chapter_id as id, c.name as name LIMIT $limit"
            result = session.run(query, {"limit": limit})
            return [{"id": record["id"], "name": record["name"]} for record in result]

    def get_chapter_by_id(self, chapter_id: str) -> Optional[Dict[str, Any]]:
        with self.driver.session(database=self.database) as session:
            query = """
            MATCH (c:Chapter {chapter_id: $chapter_id})
            RETURN c.chapter_id as id, c.name as name, c.subject as subject
            """
            result = session.run(query, {"chapter_id": chapter_id})
            record = result.single()
            return dict(record) if record else None

    def get_questions_by_chapter_name(self, chapter_name: str, limit: int = 20) -> List[Question]:
        with self.driver.session(database=self.database) as session:
            query = """
            MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q:Question)
            WHERE c.name CONTAINS $chapter_name
            RETURN q.question_id as question_id, q.content as content, q.type as type,
                   q.difficulty as difficulty, q.answer as answer, q.answer_explanation as answer_explanation,
                   q.question_number as question_number, q.options as options_json, c.name as chapter_name
            LIMIT $limit
            """
            result = session.run(query, {"chapter_name": chapter_name, "limit": limit})
            questions = []
            for record in result:
                questions.append(self._record_to_question(record))
            return questions

    def get_questions_by_chapter_id(self, chapter_id: str, limit: int = 20) -> List[Question]:
        with self.driver.session(database=self.database) as session:
            query = """
            MATCH (c:Chapter {chapter_id: $chapter_id})-[:CONTAINS_QUESTION]->(q:Question)
            RETURN q.question_id as question_id, q.content as content, q.type as type,
                   q.difficulty as difficulty, q.answer as answer, q.answer_explanation as answer_explanation,
                   q.question_number as question_number, q.options as options_json, c.name as chapter_name
            LIMIT $limit
            """
            result = session.run(query, {"chapter_id": chapter_id, "limit": limit})
            questions = []
            for record in result:
                questions.append(self._record_to_question(record))
            return questions

    def get_questions_by_chapter(self, chapter_identifier: str, identifier_type: str = "name", limit: int = 20) -> List[Question]:
        if identifier_type == "id":
            return self.get_questions_by_chapter_id(chapter_identifier, limit)
        else:
            return self.get_questions_by_chapter_name(chapter_identifier, limit)

    def get_knowledge_points_by_chapter_name(self, chapter_name: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self.driver.session(database=self.database) as session:
            # 导入器创建了 CONTAINS_KNOWLEDGE_POINT 关系
            query = """
            MATCH (c:Chapter)-[:CONTAINS_KNOWLEDGE_POINT]->(kp:KnowledgePoint)
            WHERE c.name CONTAINS $chapter_name
            RETURN DISTINCT kp.knowledge_point_id as id, kp.name as name, kp.category as category
            LIMIT $limit
            """
            result = session.run(query, {"chapter_name": chapter_name, "limit": limit})
            return [dict(record) for record in result]

    def get_knowledge_points_by_chapter_id(self, chapter_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self.driver.session(database=self.database) as session:
            query = """
            MATCH (c:Chapter {chapter_id: $chapter_id})-[:CONTAINS_KNOWLEDGE_POINT]->(kp:KnowledgePoint)
            RETURN kp.knowledge_point_id as id, kp.name as name, kp.category as category
            LIMIT $limit
            """
            result = session.run(query, {"chapter_id": chapter_id, "limit": limit})
            return [dict(record) for record in result]

    def get_knowledge_points_by_chapter(self, chapter_identifier: str, identifier_type: str = "name", limit: int = 50) -> List[Dict[str, Any]]:
        if identifier_type == "id":
            return self.get_knowledge_points_by_chapter_id(chapter_identifier, limit)
        else:
            return self.get_knowledge_points_by_chapter_name(chapter_identifier, limit)

    # ---------- 难度筛选 ----------
    def get_questions_by_difficulty(self, difficulty: str, limit: int = 20, kp_id: Optional[str] = None) -> List[Question]:
        with self.driver.session(database=self.database) as session:
            if kp_id:
                query = """
                MATCH (kp:KnowledgePoint {knowledge_point_id: $kp_id})<-[:TESTS_KNOWLEDGE]-(q:Question {difficulty: $difficulty})
                OPTIONAL MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q)
                RETURN q.question_id as question_id, q.content as content, q.type as type,
                       q.difficulty as difficulty, q.answer as answer, q.answer_explanation as answer_explanation,
                       q.question_number as question_number, q.options as options_json, c.name as chapter_name
                LIMIT $limit
                """
                params = {"kp_id": kp_id, "difficulty": difficulty, "limit": limit}
            else:
                query = """
                MATCH (q:Question {difficulty: $difficulty})
                OPTIONAL MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q)
                RETURN q.question_id as question_id, q.content as content, q.type as type,
                       q.difficulty as difficulty, q.answer as answer, q.answer_explanation as answer_explanation,
                       q.question_number as question_number, q.options as options_json, c.name as chapter_name
                LIMIT $limit
                """
                params = {"difficulty": difficulty, "limit": limit}
            result = session.run(query, params)
            questions = []
            for record in result:
                questions.append(self._record_to_question(record))
            return questions

    # ---------- 知识点关系（需导入器创建了PREREQUISITE关系）----------
    def get_knowledge_point_hierarchy(self, kp_name: str, direction: str = "prerequisite") -> List[str]:
        with self.driver.session(database=self.database) as session:
            if direction == "prerequisite":
                query = """
                MATCH (kp:KnowledgePoint {name: $name})-[:PREREQUISITE]->(pre:KnowledgePoint)
                RETURN pre.name as name
                """
            else:
                query = """
                MATCH (kp:KnowledgePoint {name: $name})<-[:PREREQUISITE]-(next:KnowledgePoint)
                RETURN next.name as name
                """
            result = session.run(query, {"name": kp_name})
            return [record["name"] for record in result]

    # ---------- 复合知识点题目 ----------
    def get_questions_by_multiple_knowledge_points(self, kp_names: List[str], match_all: bool = True, limit: int = 10) -> List[Question]:
        with self.driver.session(database=self.database) as session:
            if match_all:
                query = """
                MATCH (q:Question)-[:TESTS_KNOWLEDGE]->(kp:KnowledgePoint)
                WHERE kp.name IN $kp_names
                WITH q, count(DISTINCT kp) as matched
                WHERE matched = size($kp_names)
                OPTIONAL MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q)
                RETURN q.question_id as question_id, q.content as content, q.type as type,
                       q.difficulty as difficulty, q.answer as answer, q.answer_explanation as answer_explanation,
                       q.question_number as question_number, q.options as options_json, c.name as chapter_name
                LIMIT $limit
                """
            else:
                query = """
                MATCH (q:Question)-[:TESTS_KNOWLEDGE]->(kp:KnowledgePoint)
                WHERE kp.name IN $kp_names
                OPTIONAL MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q)
                RETURN DISTINCT q.question_id as question_id, q.content as content, q.type as type,
                       q.difficulty as difficulty, q.answer as answer, q.answer_explanation as answer_explanation,
                       q.question_number as question_number, q.options as options_json, c.name as chapter_name
                LIMIT $limit
                """
            result = session.run(query, {"kp_names": kp_names, "limit": limit})
            questions = []
            for record in result:
                questions.append(self._record_to_question(record))
            return questions

    # ---------- 统计分析 ----------
    def get_statistics(self) -> Dict[str, Any]:
        with self.driver.session(database=self.database) as session:
            stats = {}
            result = session.run("MATCH (q:Question) RETURN count(q) as total_questions")
            stats["total_questions"] = result.single()["total_questions"]
            result = session.run("MATCH (kp:KnowledgePoint) RETURN count(kp) as total_kps")
            stats["total_knowledge_points"] = result.single()["total_kps"]
            result = session.run("MATCH (c:Chapter) RETURN count(c) as total_chapters")
            stats["total_chapters"] = result.single()["total_chapters"]

            result = session.run("""
                MATCH (q:Question) 
                RETURN q.difficulty as difficulty, count(q) as count
            """)
            stats["difficulty_distribution"] = {r["difficulty"]: r["count"] for r in result if r["difficulty"]}

            result = session.run("""
                MATCH (q:Question) 
                RETURN q.type as type, count(q) as count
            """)
            stats["type_distribution"] = {r["type"]: r["count"] for r in result if r["type"]}
            return stats

    # ---------- 热点知识点 ----------
    def get_hot_knowledge_points(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self.driver.session(database=self.database) as session:
            query = """
            MATCH (kp:KnowledgePoint)<-[:TESTS_KNOWLEDGE]-(q:Question)
            RETURN kp.name as name, kp.category as category, count(q) as frequency
            ORDER BY frequency DESC LIMIT $limit
            """
            result = session.run(query, {"limit": limit})
            return [dict(record) for record in result]

    # ---------- 答案关键词搜索 ----------
    def search_by_answer_keyword(self, keyword: str, limit: int = 10) -> List[Question]:
        with self.driver.session(database=self.database) as session:
            pattern = f".*{re.escape(keyword)}.*"
            query = """
            MATCH (q:Question)
            WHERE q.answer =~ $pattern OR q.answer_explanation =~ $pattern
            OPTIONAL MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q)
            RETURN q.question_id as question_id, q.content as content, q.type as type,
                   q.difficulty as difficulty, q.answer as answer, q.answer_explanation as answer_explanation,
                   q.question_number as question_number, q.options as options_json, c.name as chapter_name
            LIMIT $limit
            """
            result = session.run(query, {"pattern": pattern, "limit": limit})
            questions = []
            for record in result:
                questions.append(self._record_to_question(record))
            return questions

    # ---------- 相似题目推荐 ----------
    def get_similar_questions(self, question_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        with self.driver.session(database=self.database) as session:
            query = """
            MATCH (q1:Question {question_id: $qid})-[:TESTS_KNOWLEDGE]->(kp:KnowledgePoint)<-[:TESTS_KNOWLEDGE]-(q2:Question)
            WHERE q1 <> q2
            WITH q2, count(kp) as common_kps
            RETURN q2.question_id as question_id, q2.content as content, q2.difficulty as difficulty, common_kps
            ORDER BY common_kps DESC LIMIT $limit
            """
            result = session.run(query, {"qid": question_id, "limit": limit})
            return [dict(record) for record in result]

# -------------------- 大模型工具调用代理（强化版，支持多轮工具调用）--------------------
class ToolCallingAgent:
    def __init__(self, retriever: IntelligentNeo4jRetriever):
        self.retriever = retriever
        self.tool_map = {
            "find_knowledge_points": self.tool_find_knowledge_points,
            "get_questions_by_knowledge_point": self.tool_get_questions_by_knowledge_point,
            "search_questions": self.tool_search_questions,
            "get_all_knowledge_points": self.tool_get_all_knowledge_points,
            "suggest_similar_knowledge_points": self.tool_suggest_similar_knowledge_points,
            "get_question_detail": self.tool_get_question_detail,
            "get_chapters": self.tool_get_chapters,
            "get_questions_by_chapter": self.tool_get_questions_by_chapter,
            "get_knowledge_points_by_chapter": self.tool_get_knowledge_points_by_chapter,
            "get_questions_by_difficulty": self.tool_get_questions_by_difficulty,
            "get_knowledge_point_hierarchy": self.tool_get_knowledge_point_hierarchy,
            "get_questions_by_multiple_kps": self.tool_get_questions_by_multiple_kps,
            "get_statistics": self.tool_get_statistics,
            "get_hot_knowledge_points": self.tool_get_hot_knowledge_points,
            "get_similar_questions": self.tool_get_similar_questions,
            "search_by_answer_keyword": self.tool_search_by_answer_keyword,
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

    # ---------- 工具函数实现 ----------
    def tool_find_knowledge_points(self, user_input: str) -> str:
        kps = self.retriever.find_knowledge_point_smart(user_input)
        if not kps:
            return "未找到相关知识点。"
        lines = ["找到以下知识点："]
        for i, kp in enumerate(kps[:10], 1):
            lines.append(f"{i}. {kp['name']}（类别：{kp.get('category','未知')}）")
        return "\n".join(lines)

    def tool_get_questions_by_knowledge_point(self, kp_name: str, limit: int = 10) -> str:
        kps = self.retriever.find_knowledge_point_smart(kp_name)
        if not kps:
            return f"未找到知识点「{kp_name}」。"
        kp = kps[0]
        questions = self.retriever.get_questions_by_knowledge_point_id(kp["id"], limit)
        if not questions:
            return f"知识点「{kp['name']}」暂无关联题目。"
        return self._format_questions(questions, f"知识点「{kp['name']}」")

    def tool_search_questions(self, keyword: str, limit: int = 10) -> str:
        questions = self.retriever.search_questions_in_content(keyword, limit)
        if not questions:
            return f"未找到包含「{keyword}」的题目。"
        return self._format_questions(questions, f"关键词「{keyword}」")

    def tool_get_all_knowledge_points(self, limit: int = 50) -> str:
        kps = self.retriever.get_all_knowledge_points(limit)
        if not kps:
            return "数据库中没有知识点。"
        lines = [f"📚 共有 {len(kps)} 个知识点："]
        for i, kp in enumerate(kps, 1):
            lines.append(f"{i}. {kp['name']}（{kp.get('category','未知')}） - {kp.get('question_count',0)}题")
        return "\n".join(lines)

    def tool_suggest_similar_knowledge_points(self, user_input: str, limit: int = 10) -> str:
        sugs = self.retriever.suggest_similar_knowledge_points(user_input, limit)
        if not sugs:
            return "暂无相似知识点推荐。"
        lines = ["相似知识点推荐："]
        for i, kp in enumerate(sugs, 1):
            lines.append(f"{i}. {kp['name']}（匹配度：{kp.get('match_score',0)}）")
        return "\n".join(lines)

    def tool_get_question_detail(self, question_id: str) -> str:
        detail = self.retriever.get_question_detail(question_id)
        if not detail:
            return f"未找到题目ID「{question_id}」。"
        lines = [
            f"📌 题目ID：{detail['question_id']}",
            f"📄 内容：{detail['content']}",
            f"🔖 类型：{detail['type']}  难度：{detail['difficulty']}",
            f"✅ 答案：{detail['answer']}",
            f"📝 解析：{detail['answer_explanation']}",
            f"📚 所属章节：{detail['chapter_name']}",
            f"🧠 关联知识点：{', '.join(detail['knowledge_points'])}"
        ]
        if detail['options']:
            lines.append("📋 选项：")
            for k, v in detail['options'].items():
                lines.append(f"   {k}. {v}")
        return "\n".join(lines)

    def tool_get_chapters(self, limit: int = 50) -> str:
        chapters = self.retriever.get_chapters(limit)
        if not chapters:
            return "暂无章节信息。"
        lines = [f"📚 共 {len(chapters)} 个章节："]
        for i, ch in enumerate(chapters, 1):
            lines.append(f"{i}. {ch['name']} (ID: {ch['id']})")
        return "\n".join(lines)

    def tool_get_questions_by_chapter(self, chapter_name: str = None, chapter_id: str = None, limit: int = 10) -> str:
        """获取章节下的题目，支持章节名称或章节ID"""
        try:
            if chapter_id:
                chapter = self.retriever.get_chapter_by_id(chapter_id)
                if not chapter:
                    return f"未找到章节ID「{chapter_id}」。"
                questions = self.retriever.get_questions_by_chapter_id(chapter_id, limit)
                source = f"章节「{chapter['name']}」"
            elif chapter_name:
                questions = self.retriever.get_questions_by_chapter_name(chapter_name, limit)
                source = f"章节「{chapter_name}」"
            else:
                return "请提供章节名称或章节ID。"

            if not questions:
                return f"{source}下暂无题目。"
            return self._format_questions(questions, source)
        except Exception as e:
            logger.error(f"工具 get_questions_by_chapter 异常: {e}")
            return f"获取章节题目时发生错误：{str(e)}"

    def tool_get_knowledge_points_by_chapter(self, chapter_name: str = None, chapter_id: str = None, limit: int = 50) -> str:
        """获取章节下的知识点，支持章节名称或章节ID"""
        try:
            if chapter_id:
                chapter = self.retriever.get_chapter_by_id(chapter_id)
                if not chapter:
                    return f"未找到章节ID「{chapter_id}」。"
                kps = self.retriever.get_knowledge_points_by_chapter_id(chapter_id, limit)
                source = f"章节「{chapter['name']}」"
            elif chapter_name:
                kps = self.retriever.get_knowledge_points_by_chapter_name(chapter_name, limit)
                source = f"章节「{chapter_name}」"
            else:
                return "请提供章节名称或章节ID。"

            if not kps:
                return f"{source}下暂无知识点。"
            lines = [f"{source}下的知识点："]
            for i, kp in enumerate(kps[:limit], 1):
                lines.append(f"{i}. {kp['name']}（{kp.get('category','未知')}）")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"工具 get_knowledge_points_by_chapter 异常: {e}")
            return f"获取章节知识点时发生错误：{str(e)}"

    def tool_get_questions_by_difficulty(self, difficulty: str, kp_name: str = None, limit: int = 10) -> str:
        try:
            kp_id = None
            if kp_name:
                kps = self.retriever.find_knowledge_point_smart(kp_name)
                if kps:
                    kp_id = kps[0]["id"]
            questions = self.retriever.get_questions_by_difficulty(difficulty, limit, kp_id)
            if not questions:
                return f"未找到难度为「{difficulty}」的题目。" + (f" 知识点「{kp_name}」下" if kp_name else "")
            title = f"难度「{difficulty}」" + (f" - 知识点「{kp_name}」" if kp_name else "")
            return self._format_questions(questions, title)
        except Exception as e:
            logger.error(f"工具 get_questions_by_difficulty 异常: {e}")
            return f"获取题目时发生错误：{str(e)}"

    def tool_get_knowledge_point_hierarchy(self, kp_name: str, direction: str = "prerequisite") -> str:
        try:
            names = self.retriever.get_knowledge_point_hierarchy(kp_name, direction)
            if not names:
                return f"知识点「{kp_name}」没有{direction}关系。"
            dir_label = "前置知识" if direction == "prerequisite" else "后续知识"
            return f"知识点「{kp_name}」的{dir_label}：\n" + "\n".join(f"- {n}" for n in names)
        except Exception as e:
            logger.error(f"工具 get_knowledge_point_hierarchy 异常: {e}")
            return f"获取知识点关系时发生错误：{str(e)}"

    def tool_get_questions_by_multiple_kps(self, kp_names: List[str], match_all: bool = True, limit: int = 10) -> str:
        try:
            if isinstance(kp_names, str):
                kp_names = [n.strip() for n in kp_names.split(",")]
            questions = self.retriever.get_questions_by_multiple_knowledge_points(kp_names, match_all, limit)
            if not questions:
                return "未找到满足条件的题目。"
            mode = "同时包含" if match_all else "至少包含"
            title = f"{mode}知识点「{', '.join(kp_names)}」的题目"
            return self._format_questions(questions, title)
        except Exception as e:
            logger.error(f"工具 get_questions_by_multiple_kps 异常: {e}")
            return f"获取复合知识点题目时发生错误：{str(e)}"

    def tool_get_statistics(self) -> str:
        try:
            stats = self.retriever.get_statistics()
            lines = [
                "📊 数据库统计信息",
                f"总题目数：{stats['total_questions']}",
                f"总知识点数：{stats['total_knowledge_points']}",
                f"总章节数：{stats['total_chapters']}",
                "难度分布：" + ", ".join(f"{k}:{v}" for k, v in stats['difficulty_distribution'].items()),
                "题型分布：" + ", ".join(f"{k}:{v}" for k, v in stats['type_distribution'].items()),
            ]
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"工具 get_statistics 异常: {e}")
            return f"获取统计信息时发生错误：{str(e)}"

    def tool_get_hot_knowledge_points(self, limit: int = 10) -> str:
        try:
            hots = self.retriever.get_hot_knowledge_points(limit)
            if not hots:
                return "暂无数据。"
            lines = [f"🔥 高频考点 TOP{len(hots)}："]
            for i, kp in enumerate(hots, 1):
                lines.append(f"{i}. {kp['name']} - {kp['frequency']}题")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"工具 get_hot_knowledge_points 异常: {e}")
            return f"获取高频考点时发生错误：{str(e)}"

    def tool_get_similar_questions(self, question_id: str, limit: int = 5) -> str:
        try:
            sims = self.retriever.get_similar_questions(question_id, limit)
            if not sims:
                return f"未找到与题目「{question_id}」相似的题目。"
            lines = [f"🔍 与题目 {question_id} 相似的题目："]
            for i, q in enumerate(sims, 1):
                lines.append(f"{i}. {q['content'][:80]}... (共同知识点数：{q['common_kps']})")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"工具 get_similar_questions 异常: {e}")
            return f"获取相似题目时发生错误：{str(e)}"

    def tool_search_by_answer_keyword(self, keyword: str, limit: int = 10) -> str:
        try:
            questions = self.retriever.search_by_answer_keyword(keyword, limit)
            if not questions:
                return f"未在答案中找到包含「{keyword}」的题目。"
            return self._format_questions(questions, f"答案关键词「{keyword}」")
        except Exception as e:
            logger.error(f"工具 search_by_answer_keyword 异常: {e}")
            return f"搜索答案时发生错误：{str(e)}"

    def _format_questions(self, questions: List[Question], source: str) -> str:
        if not questions:
            return f"未找到相关题目。"
        total = len(questions)
        lines = [f"📚 {source} 共找到 {total} 个题目："]
        show_cnt = min(total, 10)
        for i, q in enumerate(questions[:show_cnt], 1):
            content_preview = q.content[:100] + "..." if len(q.content) > 100 else q.content
            lines.append(f"\n第{i}题：{content_preview}")
            lines.append(f"  类型：{q.type}  难度：{q.difficulty}")
            if q.answer and q.answer not in ["未找到答案", "无答案"]:
                ans = q.answer[:50] + "..." if len(q.answer) > 50 else q.answer
                lines.append(f"  答案：{ans}")
            if q.chapter_name:
                lines.append(f"  章节：{q.chapter_name}")
        if total > show_cnt:
            lines.append(f"\n... 还有 {total - show_cnt} 个题目未显示。")
        return "\n".join(lines)

    # ---------- XML解析 ----------
    def _parse_xml_tool_call(self, content: str) -> Optional[tuple]:
        """
        解析各种格式的XML工具调用，返回 (tool_name, args_dict) 或 None
        支持格式：
          1. <tool_call><tool_name>xxx</tool_name><arg_key>k</arg_key><arg_value>v</arg_value>...</tool_call>
          2. <tool_call>get_questions\n{"chapter_id":"123"}</tool_call>
          3. <tool_call>get_questions_by_chapter\n{"chapter_id":"123","limit":10}</tool_call>
        """
        pattern = r'<tool_call>(.*?)</tool_call>'
        match = re.search(pattern, content, re.DOTALL)
        if not match:
            return None
        inner = match.group(1).strip()

        # ---------- 1. 提取工具名称 ----------
        tool_name = None
        # 尝试 <tool_name> 标签
        name_match = re.search(r'<tool_name>(.*?)</tool_name>', inner, re.DOTALL)
        if name_match:
            tool_name = name_match.group(1).strip()
        else:
            # 取第一行（忽略空行）
            lines = [line.strip() for line in inner.split('\n') if line.strip()]
            if lines:
                tool_name = lines[0]
                # 如果第一行看起来像JSON（以{开头），则整个inner可能是纯JSON，没有工具名
                if tool_name.startswith('{'):
                    tool_name = None
                    lines.insert(0, '')  # 占位

        if not tool_name:
            # 尝试在JSON前提取单词
            first_word = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)', inner)
            if first_word:
                tool_name = first_word.group(1)

        if not tool_name:
            logger.warning("XML解析失败：无法提取工具名称")
            return None

        # ---------- 2. 提取参数 ----------
        args = {}
        # 优先提取 <arg_key>/<arg_value> 对
        arg_matches = re.findall(r'<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>', inner, re.DOTALL)
        if arg_matches:
            for key, value in arg_matches:
                args[key.strip()] = value.strip()
        else:
            # 尝试将剩余部分作为JSON解析
            # 移除工具名称行
            json_candidate = inner
            if not inner.startswith('{'):
                # 移除第一行
                lines = inner.split('\n', 1)
                if len(lines) > 1:
                    json_candidate = lines[1].strip()
                else:
                    json_candidate = ''
            # 尝试解析JSON
            if json_candidate:
                try:
                    # 找到第一个 { 和最后一个 }
                    start_idx = json_candidate.find('{')
                    end_idx = json_candidate.rfind('}')
                    if start_idx != -1 and end_idx != -1:
                        json_str = json_candidate[start_idx:end_idx + 1]
                        args = json.loads(json_str)
                except json.JSONDecodeError:
                    logger.debug(f"JSON解析失败: {json_candidate}")

        # 别名映射
        if tool_name in self.tool_aliases:
            tool_name = self.tool_aliases[tool_name]

        return tool_name, args

    # ---------- 核心处理方法（多轮工具调用循环）----------
    def process(self, user_query: str) -> str:
        # 处理直接输入的XML工具调用
        if user_query.strip().startswith('<tool_call>'):
            parsed = self._parse_xml_tool_call(user_query)
            if parsed:
                tool_name, args = parsed
                logger.info(f"直接执行用户输入的XML工具调用: {tool_name}, 参数: {args}")
                if tool_name in self.tool_map:
                    try:
                        tool_result = self.tool_map[tool_name](**args)
                        return f"✅ 工具执行成功：\n{tool_result}"
                    except Exception as e:
                        return f"❌ 工具执行失败：{str(e)}"
                else:
                    return f"❌ 未知工具名：{tool_name}"

        # 初始化消息列表
        system_message = {
            "role": "system",
            "content": (
                "你是一个数据库查询助手，可以通过调用工具获取信息。"
                "请根据用户问题调用合适的工具。如果一次调用不够，可以多次调用，直到收集足够信息。"
                "当信息足够时，用自然语言回答用户。"
            )
        }
        messages = [system_message, {"role": "user", "content": user_query}]

        max_turns = 5  # 防止无限循环
        turn = 0

        while turn < max_turns:
            turn += 1
            try:
                response = client.chat.completions.create(
                    model="glm-4.7",
                    messages=messages,
                    tools=self._get_tools_schema(),
                    tool_choice="auto"
                )
            except Exception as e:
                logger.error(f"模型调用失败: {e}")
                return f"❌ 大模型服务异常：{str(e)}"

            assistant_msg = response.choices[0].message
            # 将模型回复加入消息历史
            messages.append(assistant_msg.model_dump())

            tool_calls = assistant_msg.tool_calls
            logger.info(f"第{turn}轮 tool_calls: {tool_calls}")

            # 如果没有标准 tool_calls，尝试解析XML（备选）
            if not tool_calls and assistant_msg.content:
                parsed = self._parse_xml_tool_call(assistant_msg.content)
                if parsed:
                    tool_name, args = parsed
                    logger.info(f"从XML解析到工具调用: {tool_name}, 参数: {args}")
                    if tool_name in self.tool_map:
                        # 模拟标准 tool_calls 对象
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

            # 如果有工具调用，执行它们
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

                    # 将工具结果加入消息历史
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result
                    })
                # 继续循环，让模型基于新信息决定下一步
                continue
            else:
                # 没有工具调用，说明模型准备回答，返回其内容
                answer = assistant_msg.content or "（模型未返回有效回答）"
                return answer

        # 超过最大轮数，返回错误信息
        return "❌ 对话轮次过多，可能陷入循环，请重试。"

    # ---------- 工具Schema（适配双参数）----------
    def _get_tools_schema(self) -> List[Dict]:
        return [
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
                            "direction": {"type": "string", "description": "方向：prerequisite(前置知识)/successor(后续知识)", "enum": ["prerequisite", "successor"]}
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
            }
        ]



# -------------------- 交互式主程序 --------------------
def main():
    print("=" * 70)
    print("🧠 智能知识图谱查询系统（多轮工具调用版）")
    print("=" * 70)
    print("\n🔧 正在连接 Neo4j 数据库...")
    try:
        retriever = IntelligentNeo4jRetriever(
            uri=NEO4J_URI,
            username=NEO4J_USER,
            password=NEO4J_PASSWORD
        )
        print("✅ 数据库连接成功！")
        agent = ToolCallingAgent(retriever)
        print("✅ 代理初始化完成，支持多轮工具调用，可处理复杂查询。\n")
    except Exception as e:
        print(f"❌ 系统初始化失败: {e}")
        return

    while True:
        try:
            query = input("🔍 请输入问题: ").strip()
            if query.lower() in ["quit", "exit", "q"]:
                print("👋 再见！")
                break
            if not query:
                continue

            start = time.time()
            answer = agent.process(query)
            elapsed = time.time() - start

            print(f"\n🤖 {answer}")
            print(f"\n⏱️  查询耗时: {elapsed:.2f} 秒")
            print("-" * 70)

        except KeyboardInterrupt:
            print("\n\n👋 再见！")
            break
        except Exception as e:
            print(f"\n❌ 发生错误: {e}")
            import traceback
            traceback.print_exc()

    retriever.close()

retriever = IntelligentNeo4jRetriever(
            uri=NEO4J_URI,
            username=NEO4J_USER,
            password=NEO4J_PASSWORD
        )
print("✅ 数据库连接成功！")
kw= ToolCallingAgent(retriever)

if __name__ == "__main__":
    main()