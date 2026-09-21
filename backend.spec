# -*- mode: python ; coding: utf-8 -*-
"""Career Nova2 后端 PyInstaller spec(ADR 0021,onedir)。

冻结入口 = 根目录 backend_main.py(进程内 uvicorn.run(app),不冻结 uvicorn 命令行)。
产物 = onedir(否 onefile:免每次启动解压 temp、降杀毒误报、数据文件随包走 `__file__` 可解析)。
落点 = 统一产物根 {root}/dist/backend/,工作目录 {root}/build/backend/
      —— 两者都是 PyInstaller 默认(--distpath=./dist、--workpath=./build),
      故命令无需额外参数,与 vite.outDir / electron-builder.output 的 `dist/` 对齐。

三类已知坑,在此处理:
  1. 动态导入大户(langchain/langgraph/deepagents/markitdown 系)——`collect_all` 收全子模块+数据+二进制。
  2. 编译组件(curl-cffi/pypdfium2/sqlite-vec 的 .pyd/.dll)——`collect_all` 带二进制。
  3. 数据文件(app/prompts|templates|skills 的 .md/.j2/.css)——PyInstaller 默认只收 .py,
     必须显式收,且按 `app/<子目录>` 目标路径镜像包结构(render/prompts/chat 靠 `__file__` 读)。
  另:langgraph/langchain 运行时用 importlib.metadata 读版本,`copy_metadata` 收元数据防运行时炸。

骨架先行(2026-09-16 定):盖住已知大户 + 跑通起服务;个别 lazy import 留到整链真机验证时发现再补。
红线:不打包 tests/(开源测试不公开)。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = []
binaries = []
hiddenimports = []

# --- 1+2. 动态导入大户 + 编译组件:collect_all 一次收齐(datas/binaries/hiddenimports)---
# langchain*/langgraph*/deepagents:插件注册/字符串指定类名,import 图看不见。
# markitdown:docx/pdf/pptx/xlsx 解析器动态加载。
# curl-cffi/pypdfium2/lxml/sqlite-vec:编译组件(.pyd/.dll/.so)。
for pkg in (
    "langchain",
    "langchain_core",
    "langchain_openai",
    "langgraph",
    "langgraph_checkpoint",
    "langgraph_checkpoint_sqlite",
    "deepagents",
    "markitdown",
    # markitdown 的依赖缺口(打包漏收 → 上传 pdf/docx 等 500,dev 不现):
    # - magika:MarkItDown() 初始化时建它做文件类型嗅探;模型目录 models/standard_v3_3
    #   是纯数据文件(.onnx/.json),PyInstaller 默认只收 .py,必须 collect_all 收 datas。
    #   magika 是 markitdown 的 extra 依赖,collect_all("markitdown") 不会顺藤摸到,须单列。
    # - onnxruntime:magika 跑 .onnx 模型的推理引擎,编译组件(capi/*.dll/.pyd ~33MB)。
    "magika",
    "onnxruntime",
    "curl_cffi",
    "pypdfium2",
    "sqlite_vec",
    "lxml",
    "structlog",
    "sqlmodel",
):
    tmp_datas, tmp_binaries, tmp_hiddenimports = collect_all(pkg)
    datas += tmp_datas
    binaries += tmp_binaries
    hiddenimports += tmp_hiddenimports

# onnxruntime 补丁:collect_all / collect_dynamic_libs 都漏掉 capi/onnxruntime_pybind11_state.pyd
# (import onnxruntime 必加载的 Python 绑定层,.pyd 命名不在它们的扫描模式里),缺它打包后
# import onnxruntime 即炸。显式按绝对路径补进 binaries,目标目录镜像 capi/ 结构。
import onnxruntime as _ort  # spec 在打包环境跑,直接用真包定位
_ort_capi = Path(_ort.__file__).parent / "capi"
for _pyd in _ort_capi.glob("*.pyd"):
    binaries.append((str(_pyd), "onnxruntime/capi"))

# --- 元数据:importlib.metadata 读版本(langgraph/langchain 系运行时依赖)---
for pkg in (
    "langchain",
    "langchain-core",
    "langchain-openai",
    "langgraph",
    "langgraph-checkpoint-sqlite",
    "deepagents",
    "markitdown",
    "fastapi",
    "uvicorn",
    "sqlmodel",
    "pydantic",
):
    datas += copy_metadata(pkg)

# --- 3. 应用数据文件:镜像包结构收进 _internal/app/ 下 ---
# app 是项目源码(非 site-packages 已装包),collect_data_files 按已装包找会落空,故用
# 显式 (源目录, 目标前缀) 的 Tree 映射:目标前缀 = "app/<子目录>",使冻结后
# `Path(app/prompts/__file__).parent` 仍能读到同目录的 .md(render/prompts/chat 全走 __file__)。
app_datas = [
    ("app/prompts", "app/prompts"),
    ("app/templates", "app/templates"),
    ("app/skills", "app/skills"),
]
for src_dir, dest_prefix in app_datas:
    for p in Path(src_dir).rglob("*"):
        if p.is_file():
            # datas 目标 = 目录(非文件路径),文件名自动保留;子目录结构靠目标目录带相对路径。
            # (skills/grill/SKILL.md → 目标目录 app/skills/grill,文件名 SKILL.md)
            rel_dir = p.parent.relative_to(src_dir).as_posix()
            dest_dir = dest_prefix if rel_dir == "." else f"{dest_prefix}/{rel_dir}"
            datas.append((str(p), dest_dir))

a = Analysis(
    ["backend_main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests"],  # 红线:测试不进产物
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # onedir:二进制交给 COLLECT,不内嵌 exe
    name="backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # 不开 UPX:压缩 .pyd 易触发杀毒误报,且首启更慢
    console=True,  # 保留控制台:Electron 抓 stdout 日志;出错时用户可见
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="backend",
)
