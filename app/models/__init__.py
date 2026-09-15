"""数据表模型包。"""

from app.models.analysis import AnalysisReport
from app.models.crawl_state import CrawlState
from app.models.document import ResumeDocument
from app.models.fact import UserFact
from app.models.job import Interview, Job, JobFollowupEvent
from app.models.optimization import OptimizationPending, Preference
from app.models.session import ChatMessage, ChatSession
from app.models.setting import Setting
from app.models.snapshot import ResumeSnapshot

__all__ = [
    "AnalysisReport",
    "ChatMessage",
    "ChatSession",
    "CrawlState",
    "Interview",
    "Job",
    "JobFollowupEvent",
    "OptimizationPending",
    "Preference",
    "ResumeDocument",
    "ResumeSnapshot",
    "Setting",
    "UserFact",
]
