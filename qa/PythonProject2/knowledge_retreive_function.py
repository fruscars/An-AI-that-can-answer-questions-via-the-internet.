import json
import logging
import re
import sys
from typing import Dict, List, Any, Optional, Tuple
from neo4j import GraphDatabase, Driver, Session
from datetime import datetime
from dataclasses import dataclass, asdict
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class ChapterInfo:
    """章节信息"""
    chapter_id: str
    name: str
    subject: str
    total_questions: int
    total_knowledge_points: int
    total_answered: int
    file_name: str
    exam_type: str


@dataclass
class Question:
    """题目信息"""
    question_id: str
    content: str
    type: str
    difficulty: str
    answer: str
    answer_explanation: str
    question_number: str
    options: Dict[str, str]
    knowledge_points: List[str]
    source_pages: List[int]


class EnhancedNeo4jRetriever:
    """增强的 Neo4j 知识图谱检索器"""

    def __init__(self, uri: str, username: str, password: str, database: str = "neo4j"):
        """初始化连接"""
        try:
            self.driver = GraphDatabase.driver(
                uri,
                auth=(username, password),
                connection_timeout=30,
                max_connection_lifetime=3600
            )
            self.database = database
            self._test_connection()
            logger.info(f"✅ 成功连接到 Neo4j: {uri}")
        except Exception as e:
            logger.error(f"❌ 连接 Neo4j 失败: {e}")
            raise

    def _test_connection(self):
        """测试连接"""
        with self.driver.session(database=self.database) as session:
            result = session.run("RETURN 1 AS test")
            if result.single()["test"] == 1:
                return True
            raise Exception("连接测试失败")

    def close(self):
        """关闭连接"""
        if self.driver:
            self.driver.close()
            logger.info("Neo4j 连接已关闭")

    def get_database_stats(self) -> Dict[str, Any]:
        """获取数据库统计信息"""
        try:
            with self.driver.session(database=self.database) as session:
                # 节点统计
                node_query = """
                MATCH (n)
                RETURN labels(n)[0] as label, count(n) as count
                ORDER BY label
                """
                node_result = session.run(node_query)
                node_stats = {record["label"]: record["count"] for record in node_result}

                # 关系统计
                rel_query = """
                MATCH ()-[r]->()
                RETURN type(r) as type, count(r) as count
                ORDER BY type
                """
                rel_result = session.run(rel_query)
                rel_stats = {record["type"]: record["count"] for record in rel_result}

                # 题目类型统计
                type_query = """
                MATCH (q:Question)
                RETURN q.type as type, count(q) as count
                ORDER BY count DESC
                """
                type_result = session.run(type_query)
                type_stats = {record["type"]: record["count"] for record in type_result}

                return {
                    "node_statistics": node_stats,
                    "relationship_statistics": rel_stats,
                    "question_type_distribution": type_stats
                }
        except Exception as e:
            logger.error(f"获取数据库统计失败: {e}")
            return {}

    def get_all_chapters(self) -> List[ChapterInfo]:
        """获取所有章节"""
        try:
            with self.driver.session(database=self.database) as session:
                query = """
                MATCH (c:Chapter)
                OPTIONAL MATCH (c)-[:CONTAINS_QUESTION]->(q:Question)
                OPTIONAL MATCH (c)-[:CONTAINS_KNOWLEDGE_POINT]->(kp:KnowledgePoint)
                WITH c, 
                     count(DISTINCT q) as question_count,
                     count(DISTINCT kp) as kp_count,
                     sum(CASE WHEN q.answer IS NOT NULL AND q.answer <> '' AND q.answer <> '未找到答案' THEN 1 ELSE 0 END) as answered_count
                RETURN c.chapter_id as chapter_id,
                       c.name as name,
                       c.subject as subject,
                       question_count as total_questions,
                       kp_count as total_knowledge_points,
                       answered_count as total_answered,
                       c.file_name as file_name,
                       c.exam_type as exam_type
                ORDER BY c.name
                """

                result = session.run(query)
                chapters = []

                for record in result:
                    try:
                        # 处理章节名称，提取章节编号
                        name = record["name"]
                        chapter_number = self._extract_chapter_number(name)

                        chapter = ChapterInfo(
                            chapter_id=record["chapter_id"],
                            name=name,
                            subject=record.get("subject", "数据结构"),
                            total_questions=record.get("total_questions", 0),
                            total_knowledge_points=record.get("total_knowledge_points", 0),
                            total_answered=record.get("total_answered", 0),
                            file_name=record.get("file_name", ""),
                            exam_type=record.get("exam_type", "练习题")
                        )
                        chapters.append(chapter)
                    except Exception as e:
                        logger.warning(f"处理章节记录时出错: {e}")
                        continue

                logger.info(f"获取到 {len(chapters)} 个章节")
                return chapters

        except Exception as e:
            logger.error(f"获取所有章节失败: {e}")
            return []

    def _extract_chapter_number(self, chapter_name: str) -> str:
        """从章节名称中提取章节编号"""
        # 常见的中文数字
        chinese_nums = {
            '一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
            '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
            '十一': '11', '十二': '12', '十三': '13', '十四': '14', '十五': '15',
            '十六': '16', '十七': '17', '十八': '18', '十九': '19', '二十': '20'
        }

        # 尝试匹配各种格式
        patterns = [
            r'第([一二三四五六七八九十]+)[章节]',
            r'第(\d+)[章节]',
            r'([一二三四五六七八九十]+)、',
            r'(\d+)[\.、]'
        ]

        for pattern in patterns:
            match = re.search(pattern, chapter_name)
            if match:
                num_str = match.group(1)
                # 如果是中文数字，转换为阿拉伯数字
                if num_str in chinese_nums:
                    return chinese_nums[num_str]
                return num_str

        # 如果没有找到，返回原始名称
        return chapter_name

    def search_chapters_by_keyword(self, keyword: str) -> List[ChapterInfo]:
        """根据关键词搜索章节"""
        try:
            all_chapters = self.get_all_chapters()

            if not keyword:
                return all_chapters

            keyword = keyword.lower()
            matched_chapters = []

            for chapter in all_chapters:
                # 在章节名称、主题、文件名称中搜索
                search_fields = [
                    chapter.name.lower(),
                    chapter.subject.lower(),
                    chapter.file_name.lower(),
                    self._extract_chapter_number(chapter.name)
                ]

                # 检查关键词是否出现在任何字段中
                if any(keyword in field for field in search_fields):
                    matched_chapters.append(chapter)
                # 检查中文数字匹配
                elif keyword.isdigit():
                    chapter_num = self._extract_chapter_number(chapter.name)
                    if keyword == chapter_num:
                        matched_chapters.append(chapter)

            logger.info(f"关键词 '{keyword}' 搜索到 {len(matched_chapters)} 个章节")
            return matched_chapters

        except Exception as e:
            logger.error(f"搜索章节失败: {e}")
            return []

    def get_questions_by_chapter(self, chapter_id: str,
                                 question_type: str = None,
                                 difficulty: str = None,
                                 has_answer: bool = None,
                                 limit: int = 100) -> List[Question]:
        """获取指定章节的题目 - 修复版本"""
        try:
            with self.driver.session(database=self.database) as session:
                # 构建查询条件
                conditions = ["c.chapter_id = $chapter_id"]
                params = {"chapter_id": chapter_id, "limit": limit}

                if question_type:
                    conditions.append("q.type = $question_type")
                    params["question_type"] = question_type

                if difficulty:
                    conditions.append("q.difficulty = $difficulty")
                    params["difficulty"] = difficulty

                if has_answer is not None:
                    if has_answer:
                        conditions.append("q.answer IS NOT NULL AND q.answer <> '' AND q.answer <> '未找到答案'")
                    else:
                        conditions.append("(q.answer IS NULL OR q.answer = '' OR q.answer = '未找到答案')")

                # 构建完整查询
                where_clause = " AND ".join(conditions)

                # 使用更安全的排序方法，避免 CASE WHEN 语法错误
                query = f"""
                MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q:Question)
                WHERE {where_clause}
                OPTIONAL MATCH (q)-[:TESTS_KNOWLEDGE]->(kp:KnowledgePoint)
                WITH q, collect(DISTINCT kp.name) as knowledge_points
                RETURN q.question_id as question_id,
                       q.content as content,
                       q.type as type,
                       q.difficulty as difficulty,
                       q.answer as answer,
                       q.answer_explanation as answer_explanation,
                       q.question_number as question_number,
                       q.options as options_json,
                       knowledge_points as knowledge_points
                ORDER BY 
                    CASE 
                        WHEN q.question_number IS NOT NULL AND q.question_number <> '' 
                        THEN 
                            CASE 
                                WHEN q.question_number =~ '^[0-9]+$' 
                                THEN toInteger(q.question_number) 
                                ELSE 999999 
                            END
                        ELSE 999999 
                    END,
                    q.question_number
                LIMIT $limit
                """

                result = session.run(query, params)

                questions = []
                for record in result:
                    try:
                        # 解析选项
                        options_json = record.get("options_json", "{}")
                        try:
                            options = json.loads(options_json)
                        except:
                            options = {}

                        # 解析源页面
                        source_pages = []  # 需要根据实际数据结构调整

                        question = Question(
                            question_id=record["question_id"],
                            content=record["content"],
                            type=record.get("type", "简答题"),
                            difficulty=record.get("difficulty", "中等"),
                            answer=record.get("answer", ""),
                            answer_explanation=record.get("answer_explanation", ""),
                            question_number=record.get("question_number", ""),
                            options=options,
                            knowledge_points=record.get("knowledge_points", []),
                            source_pages=source_pages
                        )
                        questions.append(question)
                    except Exception as e:
                        logger.warning(f"处理题目记录时出错: {e}")
                        continue

                logger.info(f"获取到 {len(questions)} 个题目")
                return questions

        except Exception as e:
            logger.error(f"获取章节题目失败: {e}")
            # 尝试使用简化查询
            return self._get_questions_by_chapter_simple(chapter_id, limit)

    def _get_questions_by_chapter_simple(self, chapter_id: str, limit: int = 100) -> List[Question]:
        """获取指定章节的题目 - 简化版本，避免复杂查询"""
        try:
            with self.driver.session(database=self.database) as session:
                # 使用简化查询，避免复杂的排序
                query = """
                MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q:Question)
                WHERE c.chapter_id = $chapter_id
                OPTIONAL MATCH (q)-[:TESTS_KNOWLEDGE]->(kp:KnowledgePoint)
                WITH q, collect(DISTINCT kp.name) as knowledge_points
                RETURN q.question_id as question_id,
                       q.content as content,
                       q.type as type,
                       q.difficulty as difficulty,
                       q.answer as answer,
                       q.answer_explanation as answer_explanation,
                       q.question_number as question_number,
                       q.options as options_json,
                       knowledge_points as knowledge_points
                LIMIT $limit
                """

                result = session.run(query, {"chapter_id": chapter_id, "limit": limit})

                questions = []
                for record in result:
                    try:
                        # 解析选项
                        options_json = record.get("options_json", "{}")
                        try:
                            options = json.loads(options_json)
                        except:
                            options = {}

                        question = Question(
                            question_id=record["question_id"],
                            content=record["content"],
                            type=record.get("type", "简答题"),
                            difficulty=record.get("difficulty", "中等"),
                            answer=record.get("answer", ""),
                            answer_explanation=record.get("answer_explanation", ""),
                            question_number=record.get("question_number", ""),
                            options=options,
                            knowledge_points=record.get("knowledge_points", []),
                            source_pages=[]
                        )
                        questions.append(question)
                    except Exception as e:
                        logger.warning(f"处理题目记录时出错: {e}")
                        continue

                # 在Python端进行排序
                questions.sort(key=lambda q: self._extract_question_number_for_sorting(q.question_number))

                logger.info(f"获取到 {len(questions)} 个题目（简化查询）")
                return questions

        except Exception as e:
            logger.error(f"简化查询章节题目失败: {e}")
            return []

    def _extract_question_number_for_sorting(self, question_number: str) -> int:
        """提取题目编号用于排序"""
        if not question_number:
            return 999999

        try:
            # 尝试提取数字
            match = re.search(r'(\d+)', question_number)
            if match:
                return int(match.group(1))
        except:
            pass

        return 999999

    def search_questions(self, keyword: str,
                         chapter_id: str = None,
                         question_type: str = None,
                         limit: int = 50) -> List[Question]:
        """搜索题目 - 修复版本"""
        try:
            with self.driver.session(database=self.database) as session:
                # 构建查询条件
                conditions = []
                params = {"limit": limit}

                if keyword:
                    # 使用正则表达式进行模糊匹配
                    keyword_pattern = f".*{re.escape(keyword)}.*"
                    conditions.append("q.content =~ $keyword_pattern")
                    params["keyword_pattern"] = keyword_pattern

                if chapter_id:
                    conditions.append("c.chapter_id = $chapter_id")
                    params["chapter_id"] = chapter_id

                if question_type:
                    conditions.append("q.type = $question_type")
                    params["question_type"] = question_type

                where_clause = " AND ".join(conditions) if conditions else "1=1"

                # 构建查询
                query = f"""
                MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q:Question)
                WHERE {where_clause}
                OPTIONAL MATCH (q)-[:TESTS_KNOWLEDGE]->(kp:KnowledgePoint)
                WITH c, q, collect(DISTINCT kp.name) as knowledge_points
                RETURN q.question_id as question_id,
                       q.content as content,
                       q.type as type,
                       q.difficulty as difficulty,
                       q.answer as answer,
                       q.answer_explanation as answer_explanation,
                       q.question_number as question_number,
                       q.options as options_json,
                       knowledge_points as knowledge_points,
                       c.name as chapter_name
                ORDER BY q.difficulty
                LIMIT $limit
                """

                result = session.run(query, params)

                questions = []
                for record in result:
                    try:
                        # 解析选项
                        options_json = record.get("options_json", "{}")
                        try:
                            options = json.loads(options_json)
                        except:
                            options = {}

                        question = Question(
                            question_id=record["question_id"],
                            content=record["content"],
                            type=record.get("type", "简答题"),
                            difficulty=record.get("difficulty", "中等"),
                            answer=record.get("answer", ""),
                            answer_explanation=record.get("answer_explanation", ""),
                            question_number=record.get("question_number", ""),
                            options=options,
                            knowledge_points=record.get("knowledge_points", []),
                            source_pages=[]
                        )
                        questions.append(question)
                    except Exception as e:
                        logger.warning(f"处理搜索结果时出错: {e}")
                        continue

                logger.info(f"搜索到 {len(questions)} 个题目")
                return questions

        except Exception as e:
            logger.error(f"搜索题目失败: {e}")
            return []

    def get_knowledge_points(self, chapter_id: str = None,
                             category: str = None,
                             limit: int = 100) -> List[Dict[str, Any]]:
        """获取知识点"""
        try:
            with self.driver.session(database=self.database) as session:
                # 构建查询条件
                conditions = []
                params = {"limit": limit}

                if chapter_id:
                    conditions.append("c.chapter_id = $chapter_id")
                    params["chapter_id"] = chapter_id

                if category:
                    conditions.append("kp.category = $category")
                    params["category"] = category

                where_clause = " AND ".join(conditions) if conditions else "1=1"

                query = f"""
                MATCH (c:Chapter)-[:CONTAINS_KNOWLEDGE_POINT]->(kp:KnowledgePoint)
                WHERE {where_clause}
                OPTIONAL MATCH (q:Question)-[:TESTS_KNOWLEDGE]->(kp)
                WITH kp, count(DISTINCT q) as question_count
                RETURN kp.knowledge_point_id as kp_id,
                       kp.name as name,
                       kp.category as category,
                       question_count
                ORDER BY question_count DESC
                LIMIT $limit
                """

                result = session.run(query, params)

                knowledge_points = []
                for record in result:
                    knowledge_points.append({
                        "id": record["kp_id"],
                        "name": record["name"],
                        "category": record.get("category", "数据结构"),
                        "question_count": record.get("question_count", 0)
                    })

                logger.info(f"获取到 {len(knowledge_points)} 个知识点")
                return knowledge_points

        except Exception as e:
            logger.error(f"获取知识点失败: {e}")
            return []

    def get_chapter_statistics(self, chapter_id: str) -> Dict[str, Any]:
        """获取章节详细统计"""
        try:
            with self.driver.session(database=self.database) as session:
                # 获取章节基本信息
                chapter_query = """
                MATCH (c:Chapter {chapter_id: $chapter_id})
                RETURN c.name as name,
                       c.subject as subject,
                       c.exam_type as exam_type,
                       c.file_name as file_name,
                       c.total_questions as total_questions,
                       c.total_knowledge_points as total_knowledge_points,
                       c.total_answered_questions as total_answered
                """

                chapter_result = session.run(chapter_query, {"chapter_id": chapter_id})
                chapter_info = chapter_result.single()

                if not chapter_info:
                    return {"error": "章节不存在"}

                # 题目类型统计
                type_query = """
                MATCH (c:Chapter {chapter_id: $chapter_id})-[:CONTAINS_QUESTION]->(q:Question)
                RETURN q.type as type, count(q) as count
                ORDER BY count DESC
                """

                type_result = session.run(type_query, {"chapter_id": chapter_id})
                type_stats = {record["type"]: record["count"] for record in type_result}

                # 难度统计
                difficulty_query = """
                MATCH (c:Chapter {chapter_id: $chapter_id})-[:CONTAINS_QUESTION]->(q:Question)
                RETURN q.difficulty as difficulty, count(q) as count
                ORDER BY count DESC
                """

                difficulty_result = session.run(difficulty_query, {"chapter_id": chapter_id})
                difficulty_stats = {record["difficulty"]: record["count"] for record in difficulty_result}

                # 知识点统计
                kp_query = """
                MATCH (c:Chapter {chapter_id: $chapter_id})-[:CONTAINS_KNOWLEDGE_POINT]->(kp:KnowledgePoint)
                OPTIONAL MATCH (q:Question)-[:TESTS_KNOWLEDGE]->(kp)
                WITH kp, count(DISTINCT q) as question_count
                RETURN kp.name as name, kp.category as category, question_count
                ORDER BY question_count DESC
                LIMIT 10
                """

                kp_result = session.run(kp_query, {"chapter_id": chapter_id})
                top_knowledge_points = [
                    {"name": record["name"], "category": record.get("category", ""),
                     "question_count": record["question_count"]}
                    for record in kp_result
                ]

                # 计算答案覆盖率
                answer_coverage = 0
                if chapter_info["total_questions"] > 0:
                    answer_coverage = (chapter_info["total_answered"] / chapter_info["total_questions"]) * 100

                return {
                    "chapter_info": {
                        "name": chapter_info["name"],
                        "subject": chapter_info.get("subject", "数据结构"),
                        "exam_type": chapter_info.get("exam_type", "练习题"),
                        "file_name": chapter_info.get("file_name", ""),
                        "total_questions": chapter_info.get("total_questions", 0),
                        "total_knowledge_points": chapter_info.get("total_knowledge_points", 0),
                        "total_answered": chapter_info.get("total_answered", 0),
                        "answer_coverage": f"{answer_coverage:.1f}%"
                    },
                    "question_type_distribution": type_stats,
                    "difficulty_distribution": difficulty_stats,
                    "top_knowledge_points": top_knowledge_points
                }

        except Exception as e:
            logger.error(f"获取章节统计失败: {e}")
            return {"error": str(e)}

    def get_question_details(self, question_id: str) -> Optional[Dict[str, Any]]:
        """获取题目详细信息"""
        try:
            with self.driver.session(database=self.database) as session:
                query = """
                MATCH (q:Question {question_id: $question_id})
                OPTIONAL MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q)
                OPTIONAL MATCH (q)-[:TESTS_KNOWLEDGE]->(kp:KnowledgePoint)
                WITH q, c, collect(DISTINCT kp.name) as knowledge_points
                RETURN q.question_id as question_id,
                       q.content as content,
                       q.type as type,
                       q.difficulty as difficulty,
                       q.answer as answer,
                       q.answer_explanation as answer_explanation,
                       q.question_number as question_number,
                       q.options as options_json,
                       q.has_answer as has_answer,
                       knowledge_points as knowledge_points,
                       c.name as chapter_name,
                       c.chapter_id as chapter_id
                """

                result = session.run(query, {"question_id": question_id})
                record = result.single()

                if not record:
                    return None

                # 解析选项
                options_json = record.get("options_json", "{}")
                try:
                    options = json.loads(options_json)
                except:
                    options = {}

                return {
                    "question_id": record["question_id"],
                    "content": record["content"],
                    "type": record.get("type", "简答题"),
                    "difficulty": record.get("difficulty", "中等"),
                    "answer": record.get("answer", ""),
                    "answer_explanation": record.get("answer_explanation", ""),
                    "question_number": record.get("question_number", ""),
                    "options": options,
                    "has_answer": record.get("has_answer", False),
                    "knowledge_points": record.get("knowledge_points", []),
                    "chapter_name": record.get("chapter_name", ""),
                    "chapter_id": record.get("chapter_id", "")
                }

        except Exception as e:
            logger.error(f"获取题目详情失败: {e}")
            return None

    def debug_database(self):
        """调试数据库，查看数据结构"""
        try:
            with self.driver.session(database=self.database) as session:
                print("\n" + "=" * 60)
                print("数据库调试信息")
                print("=" * 60)

                # 1. 查看所有节点标签
                print("\n1. 所有节点标签:")
                result = session.run("CALL db.labels() YIELD label RETURN label")
                labels = [record["label"] for record in result]
                for label in labels:
                    print(f"  - {label}")

                # 2. 查看所有关系类型
                print("\n2. 所有关系类型:")
                result = session.run("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType")
                rel_types = [record["relationshipType"] for record in result]
                for rel_type in rel_types:
                    print(f"  - {rel_type}")

                # 3. 查看章节数据结构
                print("\n3. 章节节点属性:")
                result = session.run("""
                MATCH (c:Chapter)
                RETURN keys(c) as keys
                LIMIT 1
                """)
                if result.peek():
                    keys = result.single()["keys"]
                    print(f"  章节属性: {', '.join(keys)}")

                # 4. 查看题目数据结构
                print("\n4. 题目节点属性:")
                result = session.run("""
                MATCH (q:Question)
                RETURN keys(q) as keys
                LIMIT 1
                """)
                if result.peek():
                    keys = result.single()["keys"]
                    print(f"  题目属性: {', '.join(keys)}")

                # 5. 查看第一章是否存在
                print("\n5. 查找第一章:")
                result = session.run("""
                MATCH (c:Chapter)
                WHERE c.name CONTAINS '第1章' OR c.name CONTAINS '第一章'
                RETURN c.name as name, c.chapter_id as id
                """)
                chapters = [(record["name"], record["id"]) for record in result]
                if chapters:
                    for name, chapter_id in chapters:
                        print(f"  找到章节: {name} (ID: {chapter_id})")
                else:
                    print("  未找到第一章")

                # 6. 查看第一个章节的题目
                print("\n6. 查看第一个章节的题目数量:")
                result = session.run("""
                MATCH (c:Chapter)-[:CONTAINS_QUESTION]->(q:Question)
                RETURN c.name as chapter_name, count(q) as question_count
                ORDER BY c.name
                LIMIT 5
                """)
                for record in result:
                    print(f"  章节: {record['chapter_name']}, 题目数: {record['question_count']}")

                print("=" * 60)

        except Exception as e:
            print(f"调试失败: {e}")


class KnowledgeGraphQueryAssistant:
    """知识图谱查询助手"""

    def __init__(self, retriever: EnhancedNeo4jRetriever):
        self.retriever = retriever
        self.history = []

    def format_chapter_list(self, chapters: List[ChapterInfo]) -> str:
        """格式化章节列表"""
        if not chapters:
            return "未找到任何章节。"

        output = f"共找到 {len(chapters)} 个章节：\n\n"
        for i, chapter in enumerate(chapters, 1):
            answer_rate = 0
            if chapter.total_questions > 0:
                answer_rate = (chapter.total_answered / chapter.total_questions) * 100

            output += f"{i}. {chapter.name}\n"
            output += f"   科目：{chapter.subject} | 题目数：{chapter.total_questions} | "
            output += f"知识点：{chapter.total_knowledge_points} | "
            output += f"答案覆盖率：{answer_rate:.1f}%\n\n"

        return output

    def format_questions(self, questions: List[Question], show_limit: int = 5) -> str:
        """格式化题目列表"""
        if not questions:
            return "未找到相关题目。"

        total = len(questions)
        output = f"共找到 {total} 个题目"

        if total > show_limit:
            output += f"，显示前 {show_limit} 个：\n\n"
            questions_to_show = questions[:show_limit]
        else:
            output += "：\n\n"
            questions_to_show = questions

        for i, q in enumerate(questions_to_show, 1):
            output += f"第 {i} 题 ({q.type} - {q.difficulty})\n"
            output += f"题目编号：{q.question_number}\n"
            output += f"题目内容：{q.content[:200]}{'...' if len(q.content) > 200 else ''}\n"

            # 显示选项（如果是选择题）
            if q.options and len(q.options) > 0:
                output += "选项：\n"
                for opt_key, opt_value in q.options.items():
                    output += f"  {opt_key}. {opt_value[:100]}{'...' if len(opt_value) > 100 else ''}\n"

            # 显示答案
            if q.answer and q.answer != "未找到答案":
                output += f"答案：{q.answer}\n"
                if q.answer_explanation:
                    output += f"答案解析：{q.answer_explanation[:200]}{'...' if len(q.answer_explanation) > 200 else ''}\n"
            else:
                output += "答案：未找到答案\n"

            # 显示关联知识点
            if q.knowledge_points:
                output += f"关联知识点：{', '.join(q.knowledge_points)}\n"

            output += "\n" + "-" * 50 + "\n\n"

        if total > show_limit:
            output += f"... 还有 {total - show_limit} 个题目未显示。\n"

        return output

    def format_knowledge_points(self, knowledge_points: List[Dict]) -> str:
        """格式化知识点列表"""
        if not knowledge_points:
            return "未找到相关知识点。"

        output = f"共找到 {len(knowledge_points)} 个知识点：\n\n"
        for i, kp in enumerate(knowledge_points, 1):
            output += f"{i}. {kp['name']}\n"
            output += f"   类别：{kp.get('category', '数据结构')} | "
            output += f"关联题目数：{kp.get('question_count', 0)}\n\n"

        return output

    def process_query(self, query: str) -> str:
        """处理用户查询"""
        try:
            # 记录查询
            self.history.append({
                "query": query,
                "timestamp": datetime.now().isoformat()
            })

            # 查询类型识别
            query_lower = query.lower()

            # 1. 查看所有章节
            if any(keyword in query_lower for keyword in ["所有章节", "章节列表", "查看章节", "有哪些章节"]):
                chapters = self.retriever.get_all_chapters()
                return self.format_chapter_list(chapters)

            # 2. 搜索章节
            elif any(keyword in query_lower for keyword in ["搜索章节", "查找章节", "章节目录"]):
                # 提取关键词
                keyword = self._extract_keyword(query, ["搜索章节", "查找章节", "章节"])
                chapters = self.retriever.search_chapters_by_keyword(keyword)
                return self.format_chapter_list(chapters)

            # 3. 获取章节统计
            elif any(keyword in query_lower for keyword in ["章节统计", "统计信息", "章节详情"]):
                # 尝试提取章节名称或编号
                chapter_patterns = [
                    r'第[一二三四五六七八九十]+章',
                    r'第\d+章',
                    r'章节[：:]\s*([^\s]+)',
                    r'关于\s*([^\s]+)\s*的统计'
                ]

                chapter_name = None
                for pattern in chapter_patterns:
                    match = re.search(pattern, query)
                    if match:
                        chapter_name = match.group(0) if pattern.startswith('第') else match.group(1)
                        break

                if chapter_name:
                    # 先找到章节
                    chapters = self.retriever.search_chapters_by_keyword(chapter_name)
                    if chapters:
                        stats = self.retriever.get_chapter_statistics(chapters[0].chapter_id)
                        return self._format_chapter_statistics(stats)
                    else:
                        return f"未找到章节：{chapter_name}"
                else:
                    return "请指定章节名称，例如：'第一章的统计信息'"

            # 4. 获取题目
            elif any(keyword in query_lower for keyword in ["题目", "习题", "练习题", "试题"]):
                return self._handle_question_query(query)

            # 5. 获取知识点
            elif any(keyword in query_lower for keyword in ["知识点", "概念", "术语"]):
                keyword = self._extract_keyword(query, ["知识点", "概念", "术语"])
                kps = self.retriever.get_knowledge_points()
                if keyword:
                    filtered_kps = [kp for kp in kps if keyword.lower() in kp["name"].lower()]
                    return self.format_knowledge_points(filtered_kps)
                else:
                    return self.format_knowledge_points(kps)

            # 6. 数据库统计
            elif any(keyword in query_lower for keyword in ["数据库统计", "数据统计", "统计报告"]):
                stats = self.retriever.get_database_stats()
                return self._format_database_stats(stats)

            # 7. 默认：搜索题目
            else:
                questions = self.retriever.search_questions(query, limit=10)
                return self.format_questions(questions)

        except Exception as e:
            logger.error(f"处理查询失败: {e}")
            return f"处理查询时出现错误：{str(e)}"

    def _extract_keyword(self, query: str, patterns: List[str]) -> str:
        """从查询中提取关键词"""
        for pattern in patterns:
            if pattern in query:
                # 提取模式后面的内容
                parts = query.split(pattern)
                if len(parts) > 1:
                    keyword = parts[1].strip()
                    # 清理标点符号
                    keyword = re.sub(r'[？?。，,；;：:]', '', keyword)
                    return keyword
        return ""

    def _handle_question_query(self, query: str) -> str:
        """处理题目查询"""
        query_lower = query.lower()

        # 提取章节信息
        chapter_patterns = [
            r'第[一二三四五六七八九十]+章',
            r'第\d+章',
            r'章节[：:]\s*([^\s]+)'
        ]

        chapter_name = None
        for pattern in chapter_patterns:
            match = re.search(pattern, query)
            if match:
                chapter_name = match.group(0) if pattern.startswith('第') else match.group(1)
                break

        # 提取题目类型
        question_type = None
        type_keywords = {
            "选择题": ["选择题", "单选", "多选"],
            "简答题": ["简答题", "简述", "简答"],
            "填空题": ["填空题", "填空"],
            "判断题": ["判断题", "判断"],
            "综合题": ["综合题", "论述", "分析"]
        }

        for qtype, keywords in type_keywords.items():
            if any(keyword in query_lower for keyword in keywords):
                question_type = qtype
                break

        # 提取难度
        difficulty = None
        difficulty_keywords = {
            "简单": ["简单", "容易", "基础"],
            "中等": ["中等", "一般"],
            "困难": ["困难", "难题", "复杂"]
        }

        for diff, keywords in difficulty_keywords.items():
            if any(keyword in query_lower for keyword in keywords):
                difficulty = diff
                break

        # 是否有答案
        has_answer = None
        if "有答案" in query_lower:
            has_answer = True
        elif "无答案" in query_lower or "没答案" in query_lower:
            has_answer = False

        # 获取题目
        if chapter_name:
            # 找到章节
            chapters = self.retriever.search_chapters_by_keyword(chapter_name)
            if chapters:
                questions = self.retriever.get_questions_by_chapter(
                    chapter_id=chapters[0].chapter_id,
                    question_type=question_type,
                    difficulty=difficulty,
                    has_answer=has_answer,
                    limit=20
                )
                return self.format_questions(questions)
            else:
                return f"未找到章节：{chapter_name}"
        else:
            # 全局搜索题目
            # 提取搜索关键词
            search_keywords = ["题目", "习题", "练习题", "试题", "问题"]
            keyword = query
            for kw in search_keywords:
                keyword = keyword.replace(kw, "").strip()

            questions = self.retriever.search_questions(
                keyword=keyword,
                question_type=question_type,
                limit=15
            )
            return self.format_questions(questions)

    def _format_chapter_statistics(self, stats: Dict[str, Any]) -> str:
        """格式化章节统计信息"""
        if "error" in stats:
            return f"获取统计信息失败：{stats['error']}"

        chapter_info = stats.get("chapter_info", {})
        type_stats = stats.get("question_type_distribution", {})
        difficulty_stats = stats.get("difficulty_distribution", {})
        top_kps = stats.get("top_knowledge_points", [])

        output = f"📊 章节统计：{chapter_info.get('name', '未知章节')}\n\n"
        output += f"科目：{chapter_info.get('subject', '数据结构')}\n"
        output += f"考试类型：{chapter_info.get('exam_type', '练习题')}\n"
        output += f"文件：{chapter_info.get('file_name', '未知文件')}\n\n"

        output += f"📈 题目统计：\n"
        output += f"  总题目数：{chapter_info.get('total_questions', 0)}\n"
        output += f"  总知识点数：{chapter_info.get('total_knowledge_points', 0)}\n"
        output += f"  有答案题目：{chapter_info.get('total_answered', 0)}\n"
        output += f"  答案覆盖率：{chapter_info.get('answer_coverage', '0%')}\n\n"

        if type_stats:
            output += f"📋 题目类型分布：\n"
            for qtype, count in type_stats.items():
                percentage = (count / chapter_info.get('total_questions', 1)) * 100
                output += f"  {qtype}：{count} 题 ({percentage:.1f}%)\n"
            output += "\n"

        if difficulty_stats:
            output += f"📊 难度分布：\n"
            for diff, count in difficulty_stats.items():
                percentage = (count / chapter_info.get('total_questions', 1)) * 100
                output += f"  {diff}：{count} 题 ({percentage:.1f}%)\n"
            output += "\n"

        if top_kps:
            output += f"🧠 高频知识点（前10）：\n"
            for i, kp in enumerate(top_kps, 1):
                output += f"  {i}. {kp.get('name', '未知')}"
                output += f" ({kp.get('category', '数据结构')})"
                output += f" - {kp.get('question_count', 0)} 题\n"

        return output

    def _format_database_stats(self, stats: Dict[str, Any]) -> str:
        """格式化数据库统计信息"""
        if not stats:
            return "数据库统计信息获取失败。"

        node_stats = stats.get("node_statistics", {})
        rel_stats = stats.get("relationship_statistics", {})
        type_stats = stats.get("question_type_distribution", {})

        total_nodes = sum(node_stats.values())
        total_rels = sum(rel_stats.values())
        total_questions = type_stats.get("total", sum(type_stats.values()))

        output = "📊 数据库统计报告\n\n"
        output += f"📦 节点总数：{total_nodes}\n"
        for label, count in node_stats.items():
            percentage = (count / total_nodes * 100) if total_nodes > 0 else 0
            output += f"  {label}：{count} ({percentage:.1f}%)\n"

        output += f"\n🔗 关系总数：{total_rels}\n"
        for rel_type, count in rel_stats.items():
            percentage = (count / total_rels * 100) if total_rels > 0 else 0
            output += f"  {rel_type}：{count} ({percentage:.1f}%)\n"

        output += f"\n❓ 题目类型分布：\n"
        for qtype, count in type_stats.items():
            if qtype != "total":
                percentage = (count / total_questions * 100) if total_questions > 0 else 0
                output += f"  {qtype}：{count} ({percentage:.1f}%)\n"

        return output


class InteractiveQueryConsole:
    """交互式查询控制台"""

    def __init__(self, assistant: KnowledgeGraphQueryAssistant):
        self.assistant = assistant
        self.running = True

    def run(self):
        """运行交互式控制台"""
        print("\n" + "=" * 70)
        print("📚 知识图谱智能查询系统 - 修复版")
        print("=" * 70)
        print("\n可用命令：")
        print("  ? 或 help      - 显示帮助信息")
        print("  chapters       - 显示所有章节")
        print("  stats          - 显示数据库统计")
        print("  debug          - 调试数据库")
        print("  history        - 显示查询历史")
        print("  quit 或 exit   - 退出程序")
        print("\n查询示例：")
        print("  第一章的所有题目")
        print("  搜索包含'树'的题目")
        print("  选择题的统计")
        print("  显示所有知识点")
        print("=" * 70)

        while self.running:
            try:
                user_input = input("\n🔍 请输入查询 (或命令): ").strip()

                if not user_input:
                    continue

                # 处理命令
                if user_input.lower() in ['quit', 'exit', 'q']:
                    print("👋 再见！")
                    self.running = False
                    continue

                elif user_input.lower() in ['?', 'help']:
                    self._show_help()
                    continue

                elif user_input.lower() == 'chapters':
                    chapters = self.assistant.retriever.get_all_chapters()
                    print(self.assistant.format_chapter_list(chapters))
                    continue

                elif user_input.lower() == 'stats':
                    stats = self.assistant.retriever.get_database_stats()
                    print(self.assistant._format_database_stats(stats))
                    continue

                elif user_input.lower() == 'debug':
                    self.assistant.retriever.debug_database()
                    continue

                elif user_input.lower() == 'history':
                    self._show_history()
                    continue

                # 处理查询
                print("\n" + "=" * 70)
                print(f"查询: {user_input}")
                print("=" * 70 + "\n")

                start_time = time.time()
                response = self.assistant.process_query(user_input)
                elapsed_time = time.time() - start_time

                print(response)
                print(f"\n⏱️  查询耗时: {elapsed_time:.2f} 秒")
                print("=" * 70)

            except KeyboardInterrupt:
                print("\n\n👋 再见！")
                self.running = False
                break
            except Exception as e:
                print(f"\n❌ 错误: {e}")

    def _show_help(self):
        """显示帮助信息"""
        help_text = """
📖 查询系统帮助手册
=======================

🔍 基本查询语法：
1. 查询章节：输入章节名称或编号，如"第一章"、"第三章 树"
2. 搜索题目：输入关键词，如"二叉树"、"排序算法"
3. 按类型查询：指定题目类型，如"选择题"、"简答题"
4. 按难度查询：指定难度级别，如"简单题目"、"难题"

📋 高级查询示例：
- "第一章的所有选择题"
- "搜索包含图的题目"
- "显示中等难度的简答题"
- "查看有答案的题目"
- "显示所有知识点"

📊 统计功能：
- "章节统计" - 查看指定章节的详细统计
- "数据库统计" - 查看整个数据库的统计信息

🐛 调试命令：
- "debug" - 查看数据库结构和调试信息

💡 提示：
- 使用中文数字或阿拉伯数字都可以
- 系统会自动匹配相似的章节名称
- 支持模糊搜索，不需要完全匹配
- 修复了CASE WHEN语法错误，现在可以正常查询
        """
        print(help_text)

    def _show_history(self):
        """显示查询历史"""
        if not self.assistant.history:
            print("还没有查询历史。")
            return

        print(f"\n📜 查询历史 (最近 {len(self.assistant.history)} 条)：\n")
        for i, item in enumerate(reversed(self.assistant.history[-10:]), 1):
            timestamp = datetime.fromisoformat(item["timestamp"]).strftime("%H:%M:%S")
            print(f"{i}. [{timestamp}] {item['query'][:50]}{'...' if len(item['query']) > 50 else ''}")

