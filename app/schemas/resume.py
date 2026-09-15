"""简历成品 JSON（结构化，JSON-Resume 词表 + layout 映射表）。

这是「整体成品」的传输模型（docs/design/resume.md §1：分散原料=user_facts，整体成品=resume JSON）。
2026-08-28 排版转向（docs/adr/0004）：生成产出从自由 Markdown 改为**受 schema 约束的结构化 JSON**，
渲染由固定模板确定性遍历字段（内容永不丢）。

字段词表采用 JSON-Resume（camelCase，与参考项目 resume-builder 的 resume.json 一致）+ 两个自造扩展：
- `certificates` 承载「证书/工具/工作技能」（参考项目里叫 otherSkills）。
- `layout` = slot → block 的**有序映射表**——方位（side-top/left-1/right-1…）与顺序是数据
  （可编辑），不是代码（不必造新模板）。调顺序 = 改 layout 列表，不动 blocks、不动引用。

方位是模板的职责、顺序是数据的职责：`blocks` 用**语义命名**（work/skills/…，引用稳定），
`layout` 用**方位槽位**去指块（调顺序零歧义）。换模板 = 换一套 slot 词表，blocks 不动。

字段名用 camelCase 直接对齐 JSON-Resume 词表——消费方（模板/下游）吃 JSON-Resume，生产方
（生成层）就吐 JSON-Resume，中间不做 snake_case↔camelCase 二次翻译。
"""

from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

# 语义块名（layout 的 block 只能指向这些；basics 默认顶部 header，也可进 layout）
BlockName = Literal[
    "basics",
    "work",
    "education",
    "skills",
    "languages",
    "certificates",
    "projects",
    "interests",
]


class Location(BaseModel):
    """所在地。JSON-Resume 标准是 city/region/countryCode；中文简历常用 city 单值。"""

    model_config = {"extra": "ignore"}

    city: str = Field(default="", description="城市")
    region: str = Field(default="", description="省/州")
    countryCode: str = Field(default="", description="国家码")


class Profile(BaseModel):
    """链接（个人主页 / 作品集 / GitHub 等）。"""

    model_config = {"extra": "ignore"}

    url: str = Field(default="", description="链接地址")
    label: str = Field(default="", description="链接名（如 GitHub）")


class Basics(BaseModel):
    """基本信息头部。name + label 是 JSON-Resume 的必填；summary 支持一句话简介。"""

    model_config = {"extra": "ignore"}

    name: str = Field(default="", description="姓名")
    label: str = Field(default="", description="一句话身份/定位（岗位标签）")
    summary: str = Field(default="", description="个人简介")
    email: str = Field(default="", description="邮箱")
    phone: str = Field(default="", description="电话")
    location: Location = Field(default_factory=Location)
    profiles: list[Profile] = Field(default_factory=list)


class WorkItem(BaseModel):
    """一段工作经历。startDate/endDate 自由格式（"2021-06"/"2024-08"/"至今"）。"""

    model_config = {"extra": "ignore"}

    name: str = Field(default="", description="公司")
    position: str = Field(default="", description="岗位")
    location: str = Field(default="", description="工作地")
    startDate: str = Field(default="", description="开始时间")
    endDate: str = Field(default="", description="结束时间")
    summary: str = Field(default="", description="这段的一句话概括")
    highlights: list[str] = Field(default_factory=list, description="成果要点")


class EducationItem(BaseModel):
    """一段教育经历。"""

    model_config = {"extra": "ignore"}

    institution: str = Field(default="", description="学校")
    area: str = Field(default="", description="专业")
    studyType: str = Field(default="", description="学历（学士/硕士）")
    startDate: str = Field(default="", description="开始时间")
    endDate: str = Field(default="", description="结束时间")


class SkillGroup(BaseModel):
    """一组技能分类（name=分类名，keywords=技能点）。"""

    model_config = {"extra": "ignore"}

    name: str = Field(default="", description="分类（如 语言/框架、DevOps、AI）")
    keywords: list[str] = Field(default_factory=list, description="技能点")


class LanguageItem(BaseModel):
    """一门语言及掌握程度。"""

    model_config = {"extra": "ignore"}

    language: str = Field(default="", description="语言（英语/…）")
    fluency: str = Field(default="", description="熟练度描述")


class CertificateItem(BaseModel):
    """证书/工具/工作技能（参考项目里叫 otherSkills）。name=技能名，issuer=描述。"""

    model_config = {"extra": "ignore"}

    name: str = Field(default="", description="证书/工具名")
    issuer: str = Field(default="", description="说明（描述/发证方）")


class ProjectItem(BaseModel):
    """一个项目经历。"""

    model_config = {"extra": "ignore"}

    name: str = Field(default="", description="项目名")
    description: str = Field(default="", description="项目简介")
    roles: list[str] = Field(default_factory=list, description="角色（如 全栈开发工程师）")
    startDate: str = Field(default="", description="开始时间")
    endDate: str = Field(default="", description="结束时间")
    url: str = Field(default="", description="链接")
    highlights: list[str] = Field(default_factory=list, description="成果要点")


class InterestItem(BaseModel):
    """业余生活。name=兴趣名，detail=一句话说明。"""

    model_config = {"extra": "ignore"}

    name: str = Field(default="", description="兴趣名")
    detail: str = Field(default="", description="一句话说明")


class LayoutSlot(BaseModel):
    """layout 的一格：方位槽位 → 语义块。列表顺序 = 渲染顺序。"""

    model_config = {"extra": "ignore"}

    slot: str = Field(default="", description="方位槽位（模板定义其语义，如 header/left/right/side-top）")
    block: BlockName = Field(..., description="语义块（work/skills/…）")


class Typography(BaseModel):
    """排版自由度配置（2026-09-02 grill 定稿）：五个数据层参数，一体落库随版本走、进导出。

    全部字段都是「排版旋钮」，渲染时注入模板 :root CSS 变量（--scale/--lh/--spacing/--ls/--gutter）：
    - `scale` 字号倍率：1.0=基准字号，乘所有 font-size。
    - `line_height` 行距倍率：乘 line-height（em 基准），默认 1.25。
    - `spacing` 段距倍率：乘块间 margin，默认 1.0。
    - `letter_spacing` 字间距（绝对 px，加性微调）：默认 0。
    - `gutter` 栏距（绝对 px）：左右两栏之间的缝隙，默认 55（原 5px+50px padding 硬撑，2026-09-02
      主两栏 table→flex 后改为单个 column-gap 值，提成可调参数）。
    对应模板 :root 的 --scale/--lh/--spacing/--ls/--gutter。

    归一化（2026-09-02 grill「手动+自动共用硬限制」，唯一真相源在此）：
    每个字段入模型即 **clamp 到合法范围 + round 到各自小数位**（静默归一，不报错）——
    手动滑杆/自动求解/任何链路进来的值，落库前都被归一成干净值，杜绝 0.8733333 这类浮点尾巴。
    范围与小数位定义在 `_BOUNDS`；scale 下界收紧到 0.5（字号 0.5 倍是可读性底线，原 gt=0 太松）。
    """

    model_config = {"extra": "ignore"}

    # (min, max, 小数位)。范围放宽到不卡校验（让越界值进来被 clamp），真正边界在归一化里收。
    scale: float = Field(default=1.0, description="字号倍率，1.0=基准，[0.5,1.5]")
    line_height: float = Field(default=1.25, description="行距倍率，[1.0,2.0]")
    spacing: float = Field(default=1.0, description="段距倍率，[0.0,2.0]")
    letter_spacing: float = Field(default=0.0, description="字间距 px，[-1.0,3.0]")
    gutter: float = Field(default=55.0, description="栏距 px（左右两栏缝隙），[0,120]")

    # 字段名 → (下界, 上界, 小数位)。clamp+round 的唯一真相源。
    _BOUNDS: ClassVar[dict[str, tuple[float, float, int]]] = {
        "scale": (0.5, 1.5, 2),
        "line_height": (1.0, 2.0, 2),
        "spacing": (0.0, 2.0, 2),
        "letter_spacing": (-1.0, 3.0, 1),
        "gutter": (0.0, 120.0, 0),
    }

    @model_validator(mode="after")
    def _normalize(self) -> "Typography":
        """Clamp + round 每个字段到其 (min, max, 小数位)。静默归一，不抛错。"""
        for name, (lo, hi, digits) in self._BOUNDS.items():
            v = getattr(self, name)
            v = min(hi, max(lo, v))
            setattr(self, name, round(v, digits))
        return self

    def to_css_vars(self) -> str:
        """渲染成 :root CSS 变量声明串（注入模板 <style>:root{...}</style>）。"""
        return (
            f"--scale: {self.scale}; --lh: {self.line_height}; "
            f"--spacing: {self.spacing}; --ls: {self.letter_spacing}px; "
            f"--gutter: {self.gutter}px"
        )


def typography_to_json(t: Typography) -> str:
    """排版配置 → JSON 字符串（存库用，紧凑）。"""
    return t.model_dump_json()


def typography_from_json(data: str) -> Typography:
    """JSON 字符串 → 排版配置（读库用）。空/坏 JSON 回退默认 Typography（宽松，不抛）。"""
    if not data.strip():
        return Typography()
    try:
        return Typography.model_validate_json(data)
    except ValueError:
        return Typography()


class Resume(BaseModel):
    """一份结构化简历（整体成品）。生成层结构化输出的 schema，也是存库的真身。"""

    model_config = {"extra": "ignore"}

    summary: str = Field(default="", description="一句话版本名（Git 意味，S8；不渲染，只落库显示）")
    basics: Basics = Field(default_factory=Basics)
    work: list[WorkItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list)
    certificates: list[CertificateItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    interests: list[InterestItem] = Field(default_factory=list)
    layout: list[LayoutSlot] = Field(default_factory=list, description="方位→块的有序映射（调顺序改这里）")


# 第一个两栏模板的默认排布（resume-builder 布局：header=basics，左主栏=work/projects，
# 右栏=education/skills/certificates/languages/interests）。block 空则模板跳过该格。
def default_layout() -> list[LayoutSlot]:
    """第一个模板（两栏）的默认 layout。生成层无显式 layout 时用它。"""
    return [
        LayoutSlot(slot="header", block="basics"),
        LayoutSlot(slot="left", block="work"),
        LayoutSlot(slot="left", block="projects"),
        LayoutSlot(slot="right", block="education"),
        LayoutSlot(slot="right", block="skills"),
        LayoutSlot(slot="right", block="certificates"),
        LayoutSlot(slot="right", block="languages"),
        LayoutSlot(slot="right", block="interests"),
    ]


def resume_to_json(resume: Resume) -> str:
    """简历 → JSON 字符串（存库用；紧凑，不带缩进省空间）。"""
    return resume.model_dump_json()


def resume_from_json(data: str) -> Resume:
    """JSON 字符串 → 简历（读库用）。空/坏 JSON 抛 ValueError（调用方收口）。"""
    return Resume.model_validate_json(data)
