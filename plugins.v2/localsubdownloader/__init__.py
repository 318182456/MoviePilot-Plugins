import hashlib
import io
import json
import zipfile
import datetime
import threading
from functools import lru_cache
from pathlib import Path
from typing import List, Tuple, Dict, Any

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TransferInfo
from app.schemas.types import EventType
from fastapi import Request
from app.core.plugin import PluginManager


def normalize_path(p) -> str:
    if not p:
        return ""
    p_str = str(Path(p).resolve()).replace("\\", "/")
    if p_str.endswith("/") and len(p_str) > 1:
        p_str = p_str[:-1]
    return p_str

class FakeResponse:
    def __init__(self, status_code: int, content: bytes, text: str = None):
        self.status_code = status_code
        self.content = content
        self.text = text if text is not None else (content.decode('utf-8', errors='ignore') if isinstance(content, bytes) else str(content))

    def json(self) -> Any:
        return json.loads(self.text)


async def global_api_manual_run(request: Request) -> Any:
    instance = PluginManager()._running_plugins.get("LocalSubDownloader")
    if instance:
        return await instance.api_manual_run(request)
    return {"code": 1, "message": "插件实例未加载"}


async def global_api_change_root(request: Request) -> Any:
    instance = PluginManager()._running_plugins.get("LocalSubDownloader")
    if instance:
        return await instance.api_change_root(request)
    return {"code": 1, "message": "插件实例未加载"}


async def global_api_go_up(request: Request) -> Any:
    instance = PluginManager()._running_plugins.get("LocalSubDownloader")
    if instance:
        return await instance.api_go_up(request)
    return {"code": 1, "message": "插件实例未加载"}


async def global_api_go_into(request: Request) -> Any:
    instance = PluginManager()._running_plugins.get("LocalSubDownloader")
    if instance:
        return await instance.api_go_into(request)
    return {"code": 1, "message": "插件实例未加载"}


async def global_api_run_selected(request: Request) -> Any:
    instance = PluginManager()._running_plugins.get("LocalSubDownloader")
    if instance:
        return await instance.api_run_selected(request)
    return {"code": 1, "message": "插件实例未加载"}


async def global_api_save_selected(request: Request) -> Any:
    instance = PluginManager()._running_plugins.get("LocalSubDownloader")
    if instance:
        return await instance.api_save_selected(request)
    return {"code": 1, "message": "插件实例未加载"}


async def global_api_toggle_video(request: Request) -> Any:
    instance = PluginManager()._running_plugins.get("LocalSubDownloader")
    if instance:
        return await instance.api_toggle_video(request)
    return {"code": 1, "message": "插件实例未加载"}


async def global_api_run_all(request: Request) -> Any:
    instance = PluginManager()._running_plugins.get("LocalSubDownloader")
    if instance:
        return await instance.api_run_all(request)
    return {"code": 1, "message": "插件实例未加载"}


async def get_request_params(request: Request) -> dict:
    """
    极具鲁棒性的参数提取辅助函数。
    能够融合处理并合并 Query Params, JSON Body 以及 Form Data，一网打尽所有请求参数。
    """
    params = {}
    
    # 1. 提取 Query Params
    try:
        if request.query_params:
            params.update({k: v for k, v in request.query_params.items()})
    except Exception:
        pass
        
    # 2. 提取 Body 参数 (JSON 格式或 Form 表单格式)
    if request.method in ("POST", "PUT", "PATCH"):
        # 尝试解析 JSON
        try:
            json_body = await request.json()
            if isinstance(json_body, dict):
                params.update(json_body)
        except Exception:
            pass
            
        # 尝试解析 Form 表单 (MultipartForm 或 FormUrlencoded)
        try:
            form_data = await request.form()
            if form_data:
                params.update({k: v for k, v in form_data.items()})
        except Exception:
            pass
            
    return params


class LocalSubDownloader(_PluginBase):
    # 插件名称
    plugin_name = "本地轻量字幕下载器"
    # 插件描述
    plugin_desc = "整理入库时自动通过射手、迅雷、ASSRT与A4k(SubDL)匹配并下载本地字幕。具有MD5内容去重与时间轴特征对齐算法。"
    # 插件图标
    plugin_icon = "subtitles.png"
    # 插件版本
    plugin_version = "2.0.2"
    # 插件作者
    plugin_author = "318182456"
    # 作者主页
    author_url = "https://github.com/318182456"
    # 插件配置项ID前缀
    plugin_config_prefix = "localsubdownloader_"
    # 加载顺序
    plugin_order = 6
    # 可使用的用户级别
    auth_level = 1

    # 内存缓存，用于避免数据库写锁、延迟以及高频读写对前台实时渲染带来的性能与可见性影响
    _logs_cache = []
    _history_cache = []
    _selected_videos_cache = []

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._only_chinese = config.get("only_chinese", True)
            self._shooter_enabled = config.get("shooter_enabled", True)
            self._xunlei_enabled = config.get("xunlei_enabled", True)
            self._assrt_enabled = config.get("assrt_enabled", False)
            self._assrt_token = config.get("assrt_token", "").strip()
            self._subdl_enabled = config.get("subdl_enabled", False)
            self._subdl_api_key = config.get("subdl_api_key", "").strip()
            try:
                self._auto_download_delay = int(config.get("auto_download_delay", 30))
            except Exception:
                self._auto_download_delay = 30

        # 初始化加载持久化日志、历史与选定视频缓存到内存，确保双保险
        try:
            self._logs_cache = self.get_data("logs") or []
            self._history_cache = self.get_data("history") or []
            self._selected_videos_cache = self.get_data("selected_videos") or []
        except Exception:
            self._logs_cache = []
            self._history_cache = []
            self._selected_videos_cache = []

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        注册系统远程指令，支持远程或快捷方式一键触发手动整理。
        """
        return [
            {
                "cmd": "/localsubdownload",
                "event": EventType.PluginAction,
                "desc": "本地字幕下载器：一键手动整理",
                "category": "插件命令",
                "data": {
                    "action": "localsubdownloader_run",
                },
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """
        注册后台 API 终结点，处理前台的手动整理请求。
        """
        return [
            {
                "path": "/run",
                "endpoint": global_api_manual_run,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "手动运行整理字幕",
                "description": "后台异步扫描指定目录路径，为所有视频文件进行字幕爬取",
            },
            {
                "path": "/change_root",
                "endpoint": global_api_change_root,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "切换所选根目录",
                "description": "切换当前手动字幕整理的根目录",
            },
            {
                "path": "/go_up",
                "endpoint": global_api_go_up,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "返回上一级目录",
                "description": "返回当前目录的上一层级",
            },
            {
                "path": "/go_into",
                "endpoint": global_api_go_into,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "进入子目录",
                "description": "进入当前目录的子文件夹",
            },
            {
                "path": "/run_selected",
                "endpoint": global_api_run_selected,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "为所选视频下载字幕",
                "description": "后台异步为前台选中的视频文件下载字幕",
            },
            {
                "path": "/save_selected",
                "endpoint": global_api_save_selected,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "保存已勾选视频",
                "description": "保存前台多选选中的视频路径列表",
            },
            {
                "path": "/toggle_video",
                "endpoint": global_api_toggle_video,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "切换视频的勾选状态",
                "description": "前台勾选/取消勾选单个视频时触发后台缓存状态同步",
            },
            {
                "path": "/run_all",
                "endpoint": global_api_run_all,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "整理当前目录全部视频",
                "description": "后台异步为当前浏览目录下所有视频文件下载字幕",
            }
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'only_chinese',
                                            'label': '仅下载中文字幕',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'auto_download_delay',
                                            'label': '自动拉取延迟时间 (秒)',
                                            'type': 'number',
                                            'hint': '转移入库完成后等待指定时间再匹配字幕。默认 30 秒。'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'shooter_enabled',
                                            'label': '启用 射手网 (无须Token, 精准对齐)',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'xunlei_enabled',
                                            'label': '启用 迅雷字幕 (无须Token, 精准对齐)',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'assrt_enabled',
                                            'label': '启用 ASSRT (伪射手网)',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'assrt_token',
                                            'label': 'ASSRT Token (请前往assrt.net申请)'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'subdl_enabled',
                                            'label': '启用 SubDL (A4k底座)',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'subdl_api_key',
                                            'label': 'SubDL API Key (请前往subdl.com申请)'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "only_chinese": True,
            "shooter_enabled": True,
            "xunlei_enabled": True,
            "assrt_enabled": False,
            "assrt_token": "",
            "subdl_enabled": False,
            "subdl_api_key": "",
            "auto_download_delay": 30
        }

    def get_state(self) -> bool:
        return self._enabled

    def get_current_root_path(self) -> str:
        val = self.get_data("current_root_path")
        if not val:
            paths = self.get_moviepilot_media_paths()
            if paths:
                val = str(paths[0])
                self.save_data("current_root_path", val)
        return normalize_path(val)

    def get_current_dir_path(self) -> str:
        val = self.get_data("current_dir_path")
        if not val:
            val = self.get_current_root_path()
            if val:
                self.save_data("current_dir_path", val)
        return normalize_path(val)

    def get_page(self) -> List[dict]:
        """
        利用 Vuetify JSON 模式，在前台详情页渲染高颜值“手动整理控制台”、“下载历史记录表格”与“深色滚屏日志”。
        """
        # 历史和日志始终从 DB 读取，避免多 Worker 进程下内存缓存脏读
        history = self.get_data("history") or []

        logs = self.get_data("logs") or []
        # 构造表格记录行
        history_rows = []
        for idx, item in enumerate(history):
            history_rows.append({
                "index": idx + 1,
                "time": item.get("time", ""),
                "video": item.get("video", ""),
                "source": item.get("source", ""),
                "file": item.get("file", ""),
                "status": item.get("status", "")
            })

        # 取最近的 50 条日志做滚屏展示
        logs_display = logs[-50:]

        # 手动整理级联选择的核心状态
        root_paths = self.get_moviepilot_media_paths()
        current_root = self.get_current_root_path()
        current_dir = self.get_current_dir_path()
        
        # 实时从数据库加载最新已勾选的视频列表，确保多进程/多 Worker 下的状态渲染绝对一致，防范脏读
        self._selected_videos_cache = self.get_data("selected_videos") or []



        # 扫描当前浏览目录下的子目录与视频文件
        sub_dirs = []
        video_files = []
        video_extensions = {'.mp4', '.mkv', '.avi', '.ts', '.wmv', '.mov', '.flv', '.rmvb'}

        if current_dir:
            c_path = Path(current_dir)
            if c_path.exists() and c_path.is_dir():
                try:
                    for item in c_path.iterdir():
                        if item.name.startswith('.'):
                            continue
                        if item.name in ("@eaDir", "#recycle", "@tmp"):
                            continue
                        if item.is_dir():
                            sub_dirs.append(item.name)
                        elif item.is_file() and item.suffix.lower() in video_extensions:
                            video_files.append(item)
                except Exception as e:
                    logger.error(f"[LocalSubDownloader] 扫描目录 {current_dir} 失败: {e}")

            sub_dirs.sort()
            video_files.sort(key=lambda x: x.name)

        # 1. 完整定义子目录按钮磁贴列表 (解决原本未定义 dir_buttons 引起的组件 NameError)
        dir_buttons = []
        for d in sub_dirs:
            dir_buttons.append({
                'component': 'VCol',
                'props': {'cols': 6, 'md': 3, 'lg': 2},
                'content': [
                    {
                        'component': 'VBtn',
                        'text': f"📁 {d}",
                        'props': {
                            'variant': 'outlined',
                            'block': True,
                            'color': 'primary',
                            'class': 'text-none text-truncate',
                            'density': 'comfortable'
                        },
                        'events': {
                            'click': {
                                'api': 'plugin/LocalSubDownloader/go_into',
                                'method': 'post',
                                'params': {'dir_name': d}
                            }
                        }
                    }
                ]
            })
        
        if not dir_buttons:
            dir_buttons.append({
                'component': 'VCol',
                'props': {'cols': 12},
                'content': [
                    {
                        'component': 'VListItem',
                        'props': {
                            'title': '（当前目录下无子文件夹）',
                            'class': 'text-grey text-center py-2'
                        }
                    }
                ]
            })

        # 2. 构造直接显示的视频列表组件
        selected_videos = self.get_data("selected_videos") or []
        video_items = []
        if video_files:
            for v in video_files:
                existing_subs = []
                for sub_file in v.parent.glob(f"{v.stem}*"):
                    if sub_file.suffix.lower() in {'.srt', '.ass', '.vtt'}:
                        existing_subs.append(sub_file.suffix[1:].upper())
                
                v_path_str = normalize_path(str(v))
                is_selected = v_path_str in selected_videos
                
                if existing_subs:
                    sub_list_str = " / ".join(set(existing_subs))
                    subtitle_info = f"✅ 已有 ({sub_list_str})"
                else:
                    subtitle_info = "❌ 无字幕"
                
                video_items.append({
                    'component': 'VListItem',
                    'props': {'class': 'py-0 border-bottom'},
                    'content': [
                        {
                            'component': 'VRow',
                            'props': {'class': 'align-center px-1 py-0', 'no-gutters': True},
                            'content': [
                                {
                                    'component': 'VCol',
                                    'props': {'cols': 'auto'},
                                    'content': [
                                        {
                                            'component': 'VSwitch',
                                            'props': {
                                                'model-value': is_selected,
                                                'color': 'success',
                                                'density': 'compact',
                                                'hide-details': True,
                                                'class': 'ma-0 pa-0 mr-2'
                                            },
                                            'events': {
                                                'change': {
                                                    'api': 'plugin/LocalSubDownloader/toggle_video',
                                                    'method': 'post',
                                                    'params': {'video_path': v_path_str}
                                                }
                                            }
                                        }
                                    ]
                                },
                                {
                                    'component': 'VCol',
                                    'props': {'cols': True, 'class': 'text-truncate font-weight-medium text-body-2', 'style': 'max-width: calc(100% - 130px);'},
                                    'text': f"🎬 {v.name}"
                                },
                                {
                                    'component': 'VCol',
                                    'props': {'cols': 'auto', 'class': 'text-caption text-grey ml-auto pl-2'},
                                    'text': subtitle_info
                                }
                            ]
                        }
                    ]
                })

        # 根目录下拉组件数据源
        root_items = [{"title": f"📂 {p}", "value": str(p)} for p in root_paths]

        # 构造子目录导航下拉组件 (VAutocomplete，支持入力过滤)
        dir_items = [{"title": f"📁 {d}", "value": d} for d in sub_dirs]
        dir_navigation = []
        if sub_dirs:
            dir_navigation.append({
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {'cols': 12},
                        'content': [
                            {
                                'component': 'VAutocomplete',
                                'props': {
                                    'model': 'sub_dir',
                                    'label': f'🔍 输入名称/拼音以过滤当前目录下 {len(sub_dirs)} 个子文件夹 (选中即可导航进入)',
                                    'items': dir_items,
                                    'variant': 'outlined',
                                    'density': 'comfortable',
                                    'clearable': True,
                                    'prepend-inner-icon': 'mdi-folder-search'
                                },
                                'events': {
                                    'change': {
                                        'api': 'plugin/LocalSubDownloader/go_into',
                                        'method': 'post',
                                        'params': {'dir_name': '{{sub_dir}}'}
                                    }
                                }
                            }
                        ]
                    }
                ]
            })
        else:
            dir_navigation.append({
                'component': 'VListItem',
                'props': {
                    'title': '（当前目录下无子文件夹）',
                    'class': 'text-grey'
                }
            })

        # 构造视频选择及执行部分
        video_action_component = []
        if video_files:
            video_action_component = [
                {
                    'component': 'VRow',
                    'props': {'class': 'mt-2', 'dense': True},
                    'content': [
                        {
                            'component': 'VCol',
                            'props': {'cols': 12, 'sm': 6},
                            'content': [
                                {
                                    'component': 'VBtn',
                                    'text': '⚡ 整理选中视频',
                                    'props': {
                                        'color': 'success',
                                        'variant': 'elevated',
                                        'block': True,
                                        'size': 'default',
                                        'prepend-icon': 'mdi-play-selection'
                                    },
                                    'events': {
                                        'click': {
                                            'api': 'plugin/LocalSubDownloader/run_selected',
                                            'method': 'post'
                                        }
                                    }
                                }
                            ]
                        },
                        {
                            'component': 'VCol',
                            'props': {'cols': 12, 'sm': 6},
                            'content': [
                                {
                                    'component': 'VBtn',
                                    'text': '⚡ 整理当前目录全部',
                                    'props': {
                                        'color': 'primary',
                                        'variant': 'outlined',
                                        'block': True,
                                        'size': 'default',
                                        'prepend-icon': 'mdi-folder-play'
                                    },
                                    'events': {
                                        'click': {
                                            'api': 'plugin/LocalSubDownloader/run_all',
                                            'method': 'post'
                                        }
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        else:
            video_action_component = [
                {
                    'component': 'VAlert',
                    'props': {
                        'type': 'warning',
                        'variant': 'tonal',
                        'text': '💡 提示：当前目录下未发现待匹配字幕的视频文件。您可以点击上方子文件夹按钮进入下一层级。',
                        'class': 'mt-3'
                    }
                }
            ]

        return [
            # ============ 卡片1：手动整理控制台 ============
            {
                'component': 'VCard',
                'props': {'variant': 'outlined', 'class': 'mb-3'},
                'content': [
                    {
                        'component': 'VCardTitle',
                        'props': {'class': 'text-subtitle-1 font-weight-bold pa-3 pb-1'},
                        'text': '🛠️ 手动字幕整理控制台'
                    },
                    {'component': 'VDivider'},
                    {
                        'component': 'VCardText',
                        'content': [
                            {
                                'component': 'VForm',
                                'content': [

                                    # 当前路径 + 返回按钮
                                    {
                                        'component': 'VRow',
                                    'props': {'class': 'mb-2 align-center', 'dense': True},
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {'cols': 12, 'md': 9},
                                                'content': [{
                                                    'component': 'VTextField',
                                                    'props': {
                                                        'model-value': current_dir or '未选择目录',
                                                        'readonly': True,
                                                        'variant': 'outlined',
                                                        'density': 'compact',
                                                        'prepend-inner-icon': 'mdi-folder-open',
                                                        'label': '当前目录',
                                                        'hide-details': True
                                                    }
                                                }]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {'cols': 12, 'md': 3},
                                                'content': [{
                                                    'component': 'VBtn',
                                                    'text': '↑ 返回上一级',
                                                    'props': {
                                                        'variant': 'tonal',
                                                        'color': 'secondary',
                                                        'block': True,
                                                        'prepend-icon': 'mdi-arrow-up-bold',
                                                        'disabled': not current_dir or normalize_path(str(Path(current_dir).parent)) == normalize_path(current_dir)
                                                    },
                                                    'events': {
                                                        'click': {
                                                            'api': 'plugin/LocalSubDownloader/go_up',
                                                            'method': 'post'
                                                        }
                                                    }
                                                }]
                                            }
                                        ]
                                    },
                                    # 子目录磁贴按钮（硬编码 params，稳定可靠）
                                    *(
                                        [{
                                            'component': 'VRow',
                                            'props': {'dense': True, 'class': 'mb-2', 'style': 'max-height:120px;overflow-y:auto;'},
                                            'content': [
                                                {
                                                    'component': 'VCol',
                                                    'props': {'cols': 6, 'md': 4, 'lg': 3},
                                                    'content': [{
                                                        'component': 'VBtn',
                                                        'text': f'📁 {d}',
                                                        'props': {
                                                            'variant': 'tonal',
                                                            'block': True,
                                                            'color': 'primary',
                                                            'class': 'text-none text-truncate',
                                                            'density': 'comfortable',
                                                            'size': 'small'
                                                        },
                                                        'events': {
                                                            'click': {
                                                                'api': 'plugin/LocalSubDownloader/go_into',
                                                                'method': 'post',
                                                                'params': {'dir_name': d}
                                                            }
                                                        }
                                                    }]
                                                }
                                                for d in sub_dirs
                                            ]
                                        }]
                                        if sub_dirs else []
                                    ),
                                    # 视频选择区域（不用下拉，改为区域显示）
                                    *(
                                        [{
                                            'component': 'VCard',
                                            'props': {
                                                'variant': 'flat',
                                                'class': 'border rounded-lg mb-2',
                                                'style': 'max-height: 250px; overflow-y: auto;'
                                            },
                                            'content': [
                                                {
                                                    'component': 'VList',
                                                    'props': {'density': 'compact', 'class': 'py-0'},
                                                    'content': video_items
                                                }
                                            ]
                                        }]
                                        if video_files else []
                                    ),
                                    *video_action_component
                                ]
                            }
                        ]
                    }
                ]
            },
            # ============ 卡片2：历史字幕下载记录 ============
            {
                'component': 'VCard',
                'props': {'variant': 'outlined'},
                'content': [
                    {
                        'component': 'VCardTitle',
                        'props': {'class': 'text-subtitle-1 font-weight-bold pa-3 pb-1'},
                        'text': f'📜 历史字幕下载记录（共 {len(history_rows)} 条）'
                    },
                    {'component': 'VDivider'},
                    {
                        'component': 'VCardText',
                        'props': {'class': 'pa-0'},
                        'content': (
                            [{
                                'component': 'VList',
                                'props': {'density': 'compact', 'lines': 'two', 'style': 'max-height: 180px; overflow-y: auto;'},
                                'content': [
                                    item
                                    for row in list(reversed(history_rows))
                                    for item in [
                                        {
                                            'component': 'VListItem',
                                            'props': {
                                                'title': f"[{row['source']}]  {row['file']}",
                                                'subtitle': f"{row['time']}  ·  {row['video']}  ·  {row['status']}",
                                                'prepend-icon': 'mdi-check-circle-outline' if row['status'] == '成功' else 'mdi-alert-circle-outline',
                                                'class': 'py-2'
                                            }
                                        },
                                        {'component': 'VDivider'}
                                    ]
                                ]
                            }]
                            if history_rows else
                            [{
                                'component': 'VListItem',
                                'props': {
                                    'title': '暂无下载记录',
                                    'subtitle': '成功下载字幕后将在此显示历史',
                                    'prepend-icon': 'mdi-history',
                                    'class': 'text-grey py-6'
                                }
                            }]
                        )
                    }
                ]
            }
        ]

    def stop_service(self):
        pass

    # ================= 持久化日志与历史插槽 =================

    def add_log(self, msg: str):
        """
        向控制台输出日志，并记录进插件的实时日志列表中（保留最新 150 条），支持前台渲染展示。
        """
        time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{time_str}] {msg}"
        logger.info(f"[LocalSubDownloader] {msg}")

        try:
            # 双保险：同步操作内存缓存和持久化
            if not hasattr(self, "_logs_cache") or self._logs_cache is None:
                self._logs_cache = self.get_data("logs") or []
            
            self._logs_cache.append(log_line)
            if len(self._logs_cache) > 150:
                self._logs_cache = self._logs_cache[-150:]
            
            self.save_data("logs", self._logs_cache)
        except Exception as e:
            logger.error(f"[LocalSubDownloader] 追加日志失败: {e}")

    def add_history(self, video: str, source: str, file: str, status: str):
        """
        追加字幕下载历史记录到插件的 plugindata（保留最新 50 条）。
        """
        time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            if not hasattr(self, "_history_cache") or self._history_cache is None:
                self._history_cache = self.get_data("history") or []

            self._history_cache.append({
                "time": time_str,
                "video": video,
                "source": source,
                "file": file,
                "status": status
            })
            if len(self._history_cache) > 50:
                self._history_cache = self._history_cache[-50:]

            self.save_data("history", self._history_cache)
        except Exception as e:
            logger.error(f"[LocalSubDownloader] 追加下载历史失败: {e}")

    # ================= 转移完成事件监听 =================

    @eventmanager.register(EventType.TransferComplete)
    def download(self, event: Event):
        """
        监听转移完成事件，自动开始匹配和下载字幕
        """
        if not self._enabled:
            return
        item = event.event_data
        if not item:
            return

        item_transfer: TransferInfo = item.get("transferinfo")
        if not item_transfer:
            return

        # 提取转移生成的新文件列表
        item_file_list = item_transfer.file_list_new
        if not item_file_list:
            return

        # 开启异步后台线程去进行延时等待与字幕检查下载，保证不阻塞事件分发线程
        delay_seconds = getattr(self, "_auto_download_delay", 30)
        thread = threading.Thread(target=self._async_delay_download, args=(item_file_list, delay_seconds))
        thread.daemon = True
        thread.start()

    def _async_delay_download(self, file_list: List[str], delay_seconds: int):
        """
        异步后台处理线程：等待指定延迟时间，check是否已有中文字幕后进行匹配和下载
        """
        if delay_seconds > 0:
            self.add_log(f"⏰ [自动拉取延迟] 视频已整理入库，等待延迟 {delay_seconds} 秒后开始进行中文字幕校核检测...")
            import time
            time.sleep(delay_seconds)

        video_extensions = {'.mp4', '.mkv', '.avi', '.ts', '.wmv', '.mov', '.flv', '.rmvb'}
        
        for file_path_str in file_list:
            file_path = Path(file_path_str)
            if file_path.suffix.lower() in video_extensions:
                try:
                    if not file_path.exists():
                        continue

                    # 1. 检查本地是否已经有中文字幕
                    has_chinese_sub = False
                    chinese_keywords = ["zh", "cn", "chi", "chs", "cht", "双语", "中文", "简", "繁", "国语"]
                    
                    # 搜索同名外部字幕
                    for sub_file in file_path.parent.glob(f"{file_path.stem}*"):
                        if sub_file.suffix.lower() in {'.srt', '.ass', '.vtt'}:
                            sub_name = sub_file.name.lower()
                            if any(kw in sub_name for kw in chinese_keywords):
                                has_chinese_sub = True
                                break
                    
                    # 2. 如果已有中文字幕，跳过并提示
                    if has_chinese_sub:
                        self.add_log(f"📥 [自动跳过] 整理入库延时结束，检测到本地已存在中文字幕，无需重复拉取: {file_path.name}")
                        continue
                    
                    # 3. 否则，开始拉取字幕
                    self.add_log(f"🚀 [自动拉取] 整理入库延时结束，未检测到本地中文字幕，开始匹配并拉取: {file_path.name}")
                    self.process_video(file_path)
                    
                except Exception as e:
                    self.add_log(f"❌ 自动处理视频 {file_path.name} 失败: {e}")

    # ================= 手动整理/API/远程命令接口处理 =================

    @eventmanager.register(EventType.PluginAction)
    def run_command(self, event: Event):
        """
        监听一键手动整理动作（Event）触发
        """
        event_data = event.event_data or {}
        if event_data.get("action") != "localsubdownloader_run":
            return
        path_str = event_data.get("path")
        self.manual_run(path_str)

    async def api_manual_run(self, request: Request) -> Any:
        """
        前台 POST 请求调用的端点
        """
        try:
            body = await get_request_params(request)
            path_str = body.get("path")
            self.manual_run(path_str)
            return {"code": 0, "message": "手动整理任务已在后台启动，请查看下方实时运行日志"}
        except Exception as e:
            return {"code": 1, "message": f"启动整理失败: {e}"}

    async def api_change_root(self, request: Request) -> Any:
        """
        前台 POST 请求调用的端点：切换当前整理根目录
        """
        try:
            body = await get_request_params(request)
            self.add_log(f"DEBUG: api_change_root 接收到的 body 原始数据: {body}")
            root_path = body.get("root_path") or ""
            
            # 智能提取与兜底
            if not root_path or "{{root_path}}" in root_path:
                root_path = body.get("value") or ""
                
            # 智能交叉检索匹配真实物理路径
            if not root_path or "{{" in root_path:
                known_paths = [str(p) for p in self.get_moviepilot_media_paths()]
                for val in body.values():
                    val_str = str(val)
                    if val_str in known_paths:
                        root_path = val_str
                        break
                    # 二级前缀包含式提取
                    for kp in known_paths:
                        if val_str == kp or kp in val_str:
                            root_path = kp
                            break
                    if root_path:
                        break

            if root_path:
                root_path = normalize_path(root_path)
                self.save_data("current_root_path", root_path)
                self.save_data("current_dir_path", root_path)
                self.add_log(f"📌 手动整理根目录已切换为: {root_path}")
                return {"code": 0, "message": f"根目录已成功切换为: {root_path}"}
            return {"code": 1, "message": "切换根目录失败：接收到的路径为空"}
        except Exception as e:
            return {"code": 1, "message": f"切换根目录失败: {e}"}

    async def api_go_up(self, request: Request) -> Any:
        """
        前台 POST 请求调用的端点：返回上一级目录
        """
        try:
            current_dir = self.get_current_dir_path()
            if not current_dir:
                return {"code": 1, "message": "当前浏览路径为空"}
            
            path = Path(current_dir)
            parent_path = path.parent
            
            norm_parent = normalize_path(parent_path)
            norm_current = normalize_path(path)
            if norm_parent == norm_current:
                return {"code": 1, "message": "已到达系统最顶层根目录，无法继续返回上一级"}
                
            self.save_data("current_dir_path", norm_parent)
            self.add_log(f"📁 已返回上一级目录: {norm_parent}")
            return {"code": 0, "message": f"已成功返回上一级: {norm_parent}"}
        except Exception as e:
            return {"code": 1, "message": f"返回上一级失败: {e}"}

    async def api_go_into(self, request: Request) -> Any:
        """
        前台 POST 请求调用的端点：进入子目录
        """
        try:
            body = await get_request_params(request)
            dir_name = body.get("dir_name") or ""
            
            # 智能提取与兜底
            if not dir_name or "{{sub_dir}}" in dir_name:
                dir_name = body.get("value") or ""
                
            if not dir_name:
                return {"code": 1, "message": "目标文件夹名称为空"}
                
            current_dir = self.get_current_dir_path()
            if not current_dir:
                return {"code": 1, "message": "当前浏览路径为空"}
                
            next_path = Path(current_dir) / dir_name
            if next_path.exists() and next_path.is_dir():
                norm_next = normalize_path(next_path)
                self.save_data("current_dir_path", norm_next)
                self.add_log(f"📁 已进入子目录: {dir_name}")
                return {"code": 0, "message": f"已成功进入目录: {dir_name}"}
            return {"code": 1, "message": "目标文件夹不存在或不是目录"}
        except Exception as e:
            return {"code": 1, "message": f"进入子目录失败: {e}"}

    async def api_save_selected(self, request: Request) -> Any:
        """
        前台 POST 请求调用的端点：实时批量保存勾选的视频路径列表
        """
        try:
            body = await get_request_params(request)
            logger.info(f"[LocalSubDownloader] api_save_selected 接收到 Body 原始数据: {body}")
            selected = body.get("selected")
            if not selected or "{{selected_videos}}" in str(selected):
                selected = body.get("value") or body.get("videos")
            
            selected_list = []
            if isinstance(selected, list):
                selected_list = selected
            elif isinstance(selected, str):
                if selected.startswith("[") and selected.endswith("]"):
                    try:
                        selected_list = json.loads(selected)
                    except Exception:
                        selected_list = [normalize_path(v.strip()) for v in selected.split(",") if v.strip()]
                else:
                    selected_list = [normalize_path(v.strip()) for v in selected.split(",") if v.strip()]
            else:
                selected_list = []

            # 统一做 normalize_path 规整化
            self._selected_videos_cache = [normalize_path(p) for p in selected_list if p]
            self.save_data("selected_videos", self._selected_videos_cache)
            logger.info(f"[LocalSubDownloader] 联动保存视频多选缓存: 已选择 {len(self._selected_videos_cache)} 个视频")
            return {"code": 0}
        except Exception as e:
            return {"code": 1, "message": f"同步多选状态失败: {e}"}

    async def api_toggle_video(self, request: Request) -> Any:
        """
        前台 POST 请求调用的端点：单个视频 checkbox 勾选联动缓存
        """
        try:
            body = await get_request_params(request)
            video_path = body.get("video_path")
            
            if not video_path:
                return {"code": 1, "message": "视频路径为空"}
                
            norm_video = normalize_path(video_path)
            
            # 使用数据库级别的进程安全时间戳防抖，阻断一切多 Worker 进程并发重复处理的可能
            import time
            now = time.time()
            
            # 从数据库中读取全局共享的最后一次切换时间字典
            db_timestamps = self.get_data("selected_videos_toggle_timestamps") or {}
            last_time = db_timestamps.get(norm_video, 0.0)
            
            if now - last_time < 1.0: # 1秒内的极速并发一律拦截并丢弃，秒杀所有并发 BUG！
                logger.info(f"[LocalSubDownloader] 拦截多进程并发重复切换请求: {Path(norm_video).name} (间隔: {now - last_time:.3f}s)")
                return {"code": 0, "message": "并发请求已拦截"}
                
            # 立即更新并写入数据库，确保其他并发 Worker 进程秒级同步感知
            db_timestamps[norm_video] = now
            self.save_data("selected_videos_toggle_timestamps", db_timestamps)
            
            # 实时从数据库中读取最新的已勾选视频列表，杜绝内存缓存脏读
            self._selected_videos_cache = self.get_data("selected_videos") or []
            
            if norm_video in self._selected_videos_cache:
                self._selected_videos_cache.remove(norm_video)
                action_name = "取消"
            else:
                self._selected_videos_cache.append(norm_video)
                action_name = "勾选"
                    
            self.save_data("selected_videos", self._selected_videos_cache)
            logger.info(f"[LocalSubDownloader] 联动切换单个视频选择: {Path(norm_video).name} -> {action_name}")
            return {"code": 0}
        except Exception as e:
            return {"code": 1, "message": f"同步单个选择状态失败: {e}"}

    async def api_run_selected(self, request: Request) -> Any:
        """
        前台 POST 请求调用的端点：批量整理选中的视频字幕
        """
        try:
            body = await get_request_params(request)
            logger.info(f"[LocalSubDownloader] 接收到手动字幕整理请求，body: {body}")
            videos = body.get("videos") or body.get("selected_videos") or body.get("value")
            
            video_list = []
            if videos and "{{selected_videos}}" not in str(videos):
                if isinstance(videos, list):
                    video_list = videos
                elif isinstance(videos, str):
                    if videos.startswith("[") and videos.endswith("]"):
                        try:
                            video_list = json.loads(videos)
                        except Exception:
                            video_list = [v.strip() for v in videos.split(",") if v.strip()]
                    else:
                        video_list = [v.strip() for v in videos.split(",") if v.strip()]

            # 智能过滤和转换
            video_list = [normalize_path(p) for p in video_list if p and "{{selected_videos}}" not in str(p)]

            # 自动兜底读取事件联动勾选缓存，强力从数据库载入以防跨进程/跨页面数据滞后
            if not video_list:
                video_list = self.get_data("selected_videos") or getattr(self, "_selected_videos_cache", [])

            logger.info(f"[LocalSubDownloader] 待下载字幕的视频文件列表: {video_list}")

            if not video_list:
                logger.warning("[LocalSubDownloader] 字幕下载失败：未选择任何视频文件")
                return {"code": 1, "message": "请先勾选需要下载字幕的视频文件！"}
                
            # 开启异步后台线程下载，规避超时
            thread = threading.Thread(target=self._process_selected_videos, args=(video_list,))
            thread.daemon = True
            thread.start()
            
            # 成功开启字幕匹配线程后，清空已选缓存并完成持久化，使其在页面刷新后恢复未勾选状态
            self._selected_videos_cache = []
            self.save_data("selected_videos", [])
            logger.info("[LocalSubDownloader] 手动整理字幕匹配线程已启动，已清空本地勾选缓存。")
            
            return {"code": 0, "message": f"已成功启动 {len(video_list)} 个视频的字幕下载任务，请在下方观察实时运行日志"}
        except Exception as e:
            return {"code": 1, "message": f"启动批量字幕下载失败: {e}"}

    async def api_run_all(self, request: Request) -> Any:
        """
        前台 POST 请求调用的端点：整理当前目录下所有视频文件
        """
        try:
            current_dir = self.get_current_dir_path()
            if not current_dir:
                return {"code": 1, "message": "当前目录未设置，请先进入目标目录"}

            c_path = Path(current_dir)
            if not c_path.exists() or not c_path.is_dir():
                return {"code": 1, "message": f"目录不存在或无法访问：{current_dir}"}

            video_extensions = {'.mp4', '.mkv', '.avi', '.ts', '.wmv', '.mov', '.flv', '.rmvb'}
            video_list = [
                normalize_path(str(f))
                for f in c_path.iterdir()
                if f.is_file() and f.suffix.lower() in video_extensions and not f.name.startswith('.')
            ]

            if not video_list:
                return {"code": 1, "message": f"当前目录下未发现视频文件：{current_dir}"}

            logger.info(f"[LocalSubDownloader] 整理当前目录全部视频: {current_dir}，共 {len(video_list)} 个")
            thread = threading.Thread(target=self._process_selected_videos, args=(video_list,))
            thread.daemon = True
            thread.start()

            return {"code": 0, "message": f"已启动当前目录全部 {len(video_list)} 个视频的字幕下载任务，请在下方观察实时运行日志"}
        except Exception as e:
            return {"code": 1, "message": f"启动失败: {e}"}

    def _process_selected_videos(self, video_paths: List[str]):
        """
        异步后台线程执行：批量为选定的视频下载字幕
        """
        self.add_log("========================================")
        self.add_log(f"▶️ 开始为选定的 {len(video_paths)} 个视频文件下载字幕...")
        self.add_log("========================================")
        
        success_count = 0
        total = len(video_paths)
        for idx, path_str in enumerate(video_paths):
            try:
                video_path = Path(path_str)
                self.add_log(f"⏳ [{idx + 1}/{total}] 正在处理视频: {video_path.name}")
                if self.process_video(video_path):
                    success_count += 1
            except Exception as e:
                self.add_log(f"❌ 匹配 {Path(path_str).name} 字幕发生错误: {e}")
                
        self.add_log("========================================")
        self.add_log(f"🎉 选定视频字幕下载完毕！成功匹配并更新了 {success_count}/{total} 个视频的字幕")
        self.add_log("========================================")

    def manual_run(self, path_str: str = None):
        """
        开启后台线程进行目录扫描，规避网页超时挂起
        """
        thread = threading.Thread(target=self._scan_and_process, args=(path_str,))
        thread.daemon = True
        thread.start()

    def get_moviepilot_media_paths(self) -> List[Path]:
        """
        自动探测并提取 MoviePilot 中用户在数据库和后台配置的所有媒体库目录与资源整理目录。
        采用极高兼容性的动态反射机制，自动适配 V1 和 V2 各种版本中的模型更名与模块路径变更。
        """
        paths = set()

        # 策略 1：V2 版的 DirectoryHelper (最优先级)
        try:
            from app.helper.directory import DirectoryHelper
            dir_confs = DirectoryHelper.get_dirs()
            if dir_confs:
                for d in dir_confs:
                    if getattr(d, "download_path", None):
                        paths.add(Path(d.download_path))
                    if getattr(d, "library_path", None):
                        paths.add(Path(d.library_path))
                logger.info(f"[LocalSubDownloader] 从 DirectoryHelper 成功提取 {len(paths)} 个目录配置")
        except Exception as e:
            logger.debug(f"[LocalSubDownloader] 尝试使用 DirectoryHelper 失败(可能非 V2 环境): {e}")

        # 策略 2：通过 MoviePilot 系统内置的 CategoryHelper 获取分类配置 (V1/V2 早期版)
        if not paths:
            try:
                CategoryHelper = None
                import_helper_errs = []
                
                try:
                    from app.helper.category import CategoryHelper
                except Exception as e:
                    import_helper_errs.append(f"app.helper.category: {e}")
                    
                if not CategoryHelper:
                    try:
                        from app.helper import CategoryHelper
                    except Exception as e:
                        import_helper_errs.append(f"app.helper: {e}")

                if not CategoryHelper:
                    try:
                        from app.helper.transfer import CategoryHelper
                    except Exception as e:
                        import_helper_errs.append(f"app.helper.transfer: {e}")

                if not CategoryHelper:
                    try:
                        import app.helper as helper
                        for attr_name in dir(helper):
                            if "Category" in attr_name or "Helper" in attr_name:
                                attr_value = getattr(helper, attr_name)
                                if isinstance(attr_value, type):
                                    CategoryHelper = attr_value
                                    logger.info(f"[LocalSubDownloader] 动态反射识别到分类助手: {attr_name}")
                                    break
                    except Exception as e:
                        import_helper_errs.append(f"app.helper dir scan: {e}")

                if CategoryHelper:
                    categories = CategoryHelper().get_categories() or []
                    for cat in categories:
                        if isinstance(cat, dict):
                            dl_path = cat.get("download_path")
                            lib_path = cat.get("library_path")
                            if dl_path:
                                paths.add(Path(dl_path))
                            if lib_path:
                                paths.add(Path(lib_path))
                        elif hasattr(cat, "download_path"):
                            if getattr(cat, "download_path"):
                                paths.add(Path(cat.download_path))
                            if getattr(cat, "library_path"):
                                paths.add(Path(cat.library_path))
                else:
                    logger.debug(f"[LocalSubDownloader] 无法定位到 CategoryHelper 助手类，已试过所有导入路径: {import_helper_errs}")
            except Exception as e:
                logger.debug(f"[LocalSubDownloader] 通过CategoryHelper动态获取分类配置失败: {e}")

        # 策略 3：查询数据库中的分类整理模型 (V1/早期版)
        if not paths:
            try:
                from app.db import get_db
                db = next(get_db())
                
                # 动态尝试多种导入路径
                TransferCategory = None
                import_errs = []
                
                # 尝试一：原 V1/V2 常用导入路径
                try:
                    from app.db.models.transfer_category import TransferCategory
                except Exception as e:
                    import_errs.append(f"app.db.models.transfer_category: {e}")
                    
                # 尝试二：从 models 直接导入
                if not TransferCategory:
                    try:
                        from app.db.models import TransferCategory
                    except Exception as e:
                        import_errs.append(f"app.db.models: {e}")
                        
                # 尝试三：可能在 transfer 下
                if not TransferCategory:
                    try:
                        from app.db.models.transfer import TransferCategory
                    except Exception as e:
                        import_errs.append(f"app.db.models.transfer: {e}")

                # 尝试四：可能在 category 下
                if not TransferCategory:
                    try:
                        from app.db.models.category import TransferCategory
                    except Exception as e:
                        import_errs.append(f"app.db.models.category: {e}")

                # 尝试五：通过动态模块反射，深度遍历 app.db.models 寻找包含 Category 的模型类
                if not TransferCategory:
                    try:
                        import app.db.models as models
                        for attr_name in dir(models):
                            if "Category" in attr_name or "Transfer" in attr_name:
                                attr_value = getattr(models, attr_name)
                                if isinstance(attr_value, type) and hasattr(attr_value, "metadata"):
                                    TransferCategory = attr_value
                                    logger.info(f"[LocalSubDownloader] 动态反射识别到数据库分类模型: {attr_name}")
                                    break
                    except Exception as e:
                        import_errs.append(f"app.db.models dir scan: {e}")

                if TransferCategory:
                    categories = db.query(TransferCategory).all()
                    for cat in categories:
                        # 提取媒体库存储目录
                        if getattr(cat, "library_path", None):
                            paths.add(Path(cat.library_path))
                        # 提取下载资源整理目录
                        if getattr(cat, "download_path", None):
                            paths.add(Path(cat.download_path))
                else:
                    logger.debug(f"[LocalSubDownloader] 无法定位到 TransferCategory 数据库模型，已试过所有导入路径: {import_errs}")
            except Exception as e:
                logger.debug(f"[LocalSubDownloader] 通过ORM数据库动态提取分类目录失败: {e}")

        # 策略 4：从 settings 里面动态反射获取所有以 _PATH 结尾的属性
        try:
            for attr_name in dir(settings):
                if attr_name.endswith("_PATH") or "PATH" in attr_name:
                    try:
                        val = getattr(settings, attr_name)
                        if val and isinstance(val, (str, Path)):
                            paths.add(Path(str(val).strip()))
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"[LocalSubDownloader] 动态反射全局 settings 路径失败: {e}")

        # 策略 5：系统基本全局设置中兜底读取
        try:
            if hasattr(settings, "LIBRARY_PATH") and settings.LIBRARY_PATH:
                paths.add(Path(settings.LIBRARY_PATH))
            if hasattr(settings, "DOWNLOAD_PATH") and settings.DOWNLOAD_PATH:
                paths.add(Path(settings.DOWNLOAD_PATH))
        except Exception as e:
            logger.debug(f"[LocalSubDownloader] 从全局settings中读取基础路径失败: {e}")

        # 过滤校验，只保留物理存在并且有读写权限的真实文件夹路径
        valid_paths = []
        for p in paths:
            try:
                if p and p.exists() and p.is_dir():
                    # 过滤掉一些明显不是媒体库的短路径或虚拟路径，比如根目录 '/'
                    if len(p.parts) <= 1:
                        continue
                    valid_paths.append(p)
            except Exception:
                pass

        # 强行将持久化在数据库的当前手动字幕整理的根路径(current_root_path)塞入 valid_paths 首位，用以强力兜底与前台选值绑定
        try:
            db_root = self.get_data("current_root_path")
            if db_root:
                db_path = Path(db_root)
                # 即使物理上在容器中因为没有挂载或者暂不存在，为了前端 UI 能够正常加载与切换，我们也强行保留并放入最优先位置
                if db_path not in valid_paths:
                    valid_paths.insert(0, db_path)
                else:
                    # 如果本来就在里面，则调整其到首位，保证它最优先展示与选中
                    valid_paths.remove(db_path)
                    valid_paths.insert(0, db_path)
        except Exception as e:
            logger.error(f"[LocalSubDownloader] 注入当前保存的根路径兜底时报错: {e}")

        return valid_paths

    def _scan_and_process(self, path_str: str = None):
        self.add_log("========================================")
        self.add_log("▶️ 开始进行手动字幕整理...")
        self.add_log("========================================")

        scan_paths = []
        if path_str and path_str.strip():
            target_path = Path(path_str.strip())
            if target_path.exists():
                scan_paths.append(target_path)
                self.add_log(f"📌 已指定扫描整理目录路径: {target_path}")
            else:
                self.add_log(f"❌ 指定的目录路径不存在，无法扫描: {target_path}")
                self.add_log("⏹️ 手动扫描已中断")
                return
        else:
            self.add_log("🔍 未指定扫描路径，正在自动获取 MoviePilot 媒体库与分类整理目录配置...")
            scan_paths = self.get_moviepilot_media_paths()
            
            if scan_paths:
                self.add_log(f"🎯 成功识别并载入了以下 {len(scan_paths)} 个整理目录:")
                for p in scan_paths:
                    self.add_log(f" 📂 {p}")
            else:
                self.add_log("⚠️ 未能从系统数据库或配置中定位到任何有效的媒体分类整理目录")

        if not scan_paths:
            self.add_log("❌ 没有定位到任何有效的媒体扫描目录。请确认您已在 MoviePilot 目录配置中设置了分类目录，或者在上方输入正确的绝对路径。")
            self.add_log("⏹️ 手动扫描已中断")
            return

        video_extensions = {'.mp4', '.mkv', '.avi', '.ts', '.wmv', '.mov', '.flv', '.rmvb'}
        
        # 批量汇总检索到的视频文件
        video_files = []
        for path in scan_paths:
            self.add_log(f"🔍 正在递归检索目录下的所有视频文件: {path}")
            try:
                for item in path.rglob("*"):
                    if item.is_file() and item.suffix.lower() in video_extensions:
                        video_files.append(item)
            except Exception as e:
                self.add_log(f"⚠️ 检索目录 {path} 失败: {e}")

        # 去重合并
        video_files = list(set(video_files))
        total = len(video_files)
        self.add_log(f"📊 扫描完成！共定位到 {total} 个视频文件")

        success_count = 0
        for idx, video_path in enumerate(video_files):
            try:
                self.add_log(f"⏳ [{idx + 1}/{total}] 正在处理视频: {video_path.name}")
                if self.process_video(video_path):
                    success_count += 1
            except Exception as e:
                self.add_log(f"❌ 匹配 {video_path.name} 字幕发生错误: {e}")

        self.add_log("========================================")
        self.add_log(f"🎉 手动整理完毕！成功匹配并更新了 {success_count}/{total} 个视频的字幕")
        self.add_log("========================================")

        total = len(video_files)
        self.add_log(f"📊 扫描完成！共定位到 {total} 个视频文件")

        success_count = 0
        for idx, video_path in enumerate(video_files):
            try:
                self.add_log(f"⏳ [{idx + 1}/{total}] 正在处理视频: {video_path.name}")
                if self.process_video(video_path):
                    success_count += 1
            except Exception as e:
                self.add_log(f"❌ 匹配 {video_path.name} 字幕发生错误: {e}")

        self.add_log("========================================")
        self.add_log(f"🎉 手动整理完毕！成功处理了 {success_count}/{total} 个文件")
        self.add_log("========================================")

    # ================= 视频字幕匹配主干 =================

    def process_video(self, video_path: Path) -> bool:
        if not video_path.exists():
            self.add_log(f"⚠️ 视频路径不存在，跳过匹配: {video_path}")
            return False

        self.add_log(f"🔍 开始处理: {video_path.name}")

        # 1. 收集本地已有字幕 MD5 用于去重
        existing_md5s = set()
        existing_sub_names = []
        for sub_file in video_path.parent.glob(f"{video_path.stem}*"):
            if sub_file.suffix.lower() in {'.srt', '.ass', '.vtt'}:
                existing_sub_names.append(sub_file.name)
                try:
                    with open(sub_file, 'rb') as sf:
                        data = sf.read()
                    existing_md5s.add(hashlib.md5(data).hexdigest())
                except Exception:
                    pass

        if existing_sub_names:
            self.add_log(f"📂 检测到本地已有字幕: {', '.join(existing_sub_names)}")
        else:
            self.add_log(f"📂 本地无字幕文件，将尝试全部来源下载")

        # 2. 精准匹配轨道 (Hash-based)
        precision_success = False

        # 2.1 射手网精准匹配
        if self._shooter_enabled:
            try:
                shooter_hash = self.compute_shooter_hash(video_path)
                if shooter_hash:
                    self.add_log(f"🔗 [射手网] 正在Hash精准匹配...")
                    result = self.download_from_shooter(video_path, shooter_hash, existing_md5s)
                    if result:
                        precision_success = True
                    else:
                        self.add_log(f"🔗 [射手网] Hash匹配无结果")
                else:
                    self.add_log(f"🔗 [射手网] 文件过小无法计算Hash，跳过")
            except Exception as e:
                self.add_log(f"🔗 [射手网] 匹配出错: {e}")
        else:
            self.add_log(f"🔗 [射手网] 已禁用，跳过")

        # 2.2 迅雷精准匹配
        if self._xunlei_enabled:
            try:
                xunlei_cid = self.compute_xunlei_cid(video_path)
                if xunlei_cid:
                    self.add_log(f"⚡ [迅雷] 正在CID精准匹配...")
                    result = self.download_from_xunlei(video_path, xunlei_cid, existing_md5s)
                    if result:
                        precision_success = True
                    else:
                        self.add_log(f"⚡ [迅雷] CID匹配无结果")
                else:
                    self.add_log(f"⚡ [迅雷] 无法计算CID，跳过")
            except Exception as e:
                self.add_log(f"⚡ [迅雷] 匹配出错: {e}")
        else:
            self.add_log(f"⚡ [迅雷] 已禁用，跳过")

        # 3. 智能模糊评分检索轨道
        if precision_success:
            self.add_log(f"✅ Hash精准匹配已成功，跳过模糊检索")
        else:
            if existing_sub_names:
                self.add_log(f"🔎 本地已有字幕但精准匹配无新内容，继续尝试模糊检索...")

            # 3.1 ASSRT 评分检索
            if self._assrt_enabled and self._assrt_token:
                try:
                    self.add_log(f"🌐 [ASSRT] 正在智能评分检索...")
                    result = self.download_from_assrt(video_path, existing_md5s)
                    if result:
                        precision_success = True
                    else:
                        self.add_log(f"🌐 [ASSRT] 未找到匹配字幕")
                except Exception as e:
                    self.add_log(f"🌐 [ASSRT] 检索出错: {e}")
            elif self._assrt_enabled and not self._assrt_token:
                self.add_log(f"🌐 [ASSRT] Token未配置，跳过")
            else:
                self.add_log(f"🌐 [ASSRT] 已禁用，跳过")

            # 3.2 SubDL 评分检索
            if self._subdl_enabled and self._subdl_api_key:
                try:
                    self.add_log(f"🌐 [SubDL] 正在智能评分检索...")
                    result = self.download_from_subdl(video_path, existing_md5s)
                    if result:
                        precision_success = True
                    else:
                        self.add_log(f"🌐 [SubDL] 未找到匹配字幕")
                except Exception as e:
                    self.add_log(f"🌐 [SubDL] 检索出错: {e}")
            elif self._subdl_enabled and not self._subdl_api_key:
                self.add_log(f"🌐 [SubDL] API Key未配置，跳过")
            else:
                self.add_log(f"🌐 [SubDL] 已禁用，跳过")

        if not precision_success:
            self.add_log(f"❌ [{video_path.name}] 所有来源均未获取到新字幕")

        return precision_success

    # ================= 散列算法设计 =================

    @staticmethod
    def compute_shooter_hash(filepath: Path) -> str:
        try:
            size = filepath.stat().st_size
            if size < 8192:
                return ""
            offsets = [
                4096,
                size * 2 // 3,
                size // 3,
                size - 8192
            ]
            md5_hashes = []
            with open(filepath, 'rb') as f:
                for offset in offsets:
                    f.seek(offset)
                    data = f.read(4096)
                    m = hashlib.md5()
                    m.update(data)
                    md5_hashes.append(m.hexdigest())
            return ";".join(md5_hashes)
        except Exception as e:
            logger.error(f"[LocalSubDownloader] 计算 Shooter Hash 出错: {e}")
            return ""

    @staticmethod
    def compute_xunlei_cid(filepath: Path) -> str:
        try:
            size = filepath.stat().st_size
            block_size = 0x5000  # 20KB
            if size < block_size:
                with open(filepath, 'rb') as f:
                    data = f.read()
                m = hashlib.sha1()
                m.update(data)
                return m.hexdigest().upper()

            offsets = [
                0,
                size // 3,
                size - block_size
            ]
            combined_data = bytearray()
            with open(filepath, 'rb') as f:
                for offset in offsets:
                    f.seek(offset)
                    combined_data.extend(f.read(block_size))
            m = hashlib.sha1()
            m.update(combined_data)
            return m.hexdigest().upper()
        except Exception as e:
            logger.error(f"[LocalSubDownloader] 计算迅雷 CID 出错: {e}")
            return ""

    # ================= 网络请求与退化 =================

    def _http_post(self, url: str, headers: dict = None, data: dict = None, json_data: dict = None) -> Any:
        try:
            from app.utils.http import RequestUtils
            res = RequestUtils(headers=headers).post(url, data=data, json=json_data)
            if res is None:
                return None
            if isinstance(res, str):
                return FakeResponse(200, res.encode('utf-8', errors='ignore'), res)
            if not hasattr(res, "status_code"):
                return FakeResponse(200, str(res).encode('utf-8', errors='ignore'), str(res))
            return res
        except Exception:
            import urllib.request
            import urllib.parse
            req_headers = headers or {}
            payload = b""
            if json_data is not None:
                req_headers["Content-Type"] = "application/json"
                payload = json.dumps(json_data).encode('utf-8')
            elif data is not None:
                req_headers["Content-Type"] = "application/x-www-form-urlencoded"
                payload = urllib.parse.urlencode(data).encode('utf-8')
            
            req = urllib.request.Request(url, data=payload, headers=req_headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    return FakeResponse(response.getcode(), response.read())
            except Exception as e:
                logger.error(f"[LocalSubDownloader] POST 退化调用失败: {e}")
                return None

    def _http_get(self, url: str, headers: dict = None, params: dict = None) -> Any:
        try:
            from app.utils.http import RequestUtils
            res = RequestUtils(headers=headers).get(url, params=params)
            if res is None:
                return None
            if isinstance(res, str):
                return FakeResponse(200, res.encode('utf-8', errors='ignore'), res)
            if not hasattr(res, "status_code"):
                return FakeResponse(200, str(res).encode('utf-8', errors='ignore'), str(res))
            return res
        except Exception:
            import urllib.request
            import urllib.parse
            if params:
                url = url + "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers=headers or {}, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    return FakeResponse(response.getcode(), response.read())
            except Exception as e:
                logger.error(f"[LocalSubDownloader] GET 退化调用失败: {e}")
                return None

    # ================= 写入、解压与去重 =================

    def save_subtitle_stream(self, video_path: Path, filename: str, content: bytes, existing_md5s: set, source_label: str) -> bool:
        if not content:
            return False

        sub_list = []
        if content.startswith(b'PK\x03\x04'):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        ext = Path(info.filename).suffix.lower()
                        if ext in {'.srt', '.ass', '.vtt'}:
                            with zf.open(info) as f:
                                sub_list.append((Path(info.filename).name, f.read()))
            except Exception as e:
                self.add_log(f"⚠️ 解压字幕ZIP包出错: {e}")
        else:
            sub_list.append((filename, content))

        success_any = False
        for idx, (name, data) in enumerate(sub_list):
            if not data:
                continue

            sub_md5 = hashlib.md5(data).hexdigest()
            if sub_md5 in existing_md5s:
                self.add_log(f"⏭️ 字幕内容与已有文件完全相同，去重过滤跳过: {name}")
                continue

            ext = Path(name).suffix.lower() or ".srt"
            
            lang_suffix = ".zh-cn"
            if "zh-tw" in name.lower() or "cht" in name.lower() or "tc" in name.lower():
                lang_suffix = ".zh-tw"
            
            sub_idx_str = f".{idx}" if idx > 0 else ""
            target_name = f"{video_path.stem}{lang_suffix}{sub_idx_str}{ext}"
            target_path = video_path.parent / target_name

            try:
                target_path.write_bytes(data)
                existing_md5s.add(sub_md5)
                self.add_log(f"💾 [下载成功] 从【{source_label}】获取并写入本地字幕: {target_name} ({len(data)} 字节)")
                self.add_history(video=video_path.name, source=source_label, file=target_name, status="成功")
                success_any = True
            except Exception as e:
                self.add_log(f"❌ 写入字幕文件 {target_name} 失败: {e}")
                self.add_history(video=video_path.name, source=source_label, file=target_name, status=f"写入失败: {e}")

        return success_any

    # ================= 评分与对齐计算 =================

    @staticmethod
    def score_subtitle(sub_name: str, video_name: str) -> int:
        score = 0
        sub_upper = sub_name.upper()
        video_upper = video_name.upper()

        # 1. 常见压制组匹配 (加 50 分)
        groups = ["WIKI", "CHD", "CHDTV", "HEROS", "FGT", "GGWP", "CMCT", "FRDS", "LHD", "DYCX", "NGB"]
        for grp in groups:
            if grp in video_upper and grp in sub_upper:
                score += 50
                break

        # 2. 视频源特征匹配 (加 30 分)
        sources = ["WEB-DL", "WEBDL", "BLURAY", "REMUX", "HDTV", "DVDRIP"]
        for src in sources:
            if src in video_upper and src in sub_upper:
                score += 30
                break

        # 3. 分辨率与编码格式匹配 (加 20 分)
        res_enc = ["1080P", "2160P", "4K", "X265", "HEVC", "H264", "AVC"]
        for re in res_enc:
            if re in video_upper and re in sub_upper:
                score += 20

        # 4. 精确覆盖匹配
        if video_upper in sub_upper or sub_upper in video_upper:
            score += 15

        return score

    # ================= 各个字幕源具体 API 请求 =================

    def download_from_shooter(self, video_path: Path, filehash: str, existing_md5s: set) -> bool:
        url = "https://www.shooter.cn/api/subapi.php"
        data = {
            "filehash": filehash,
            "pathinfo": video_path.name,
            "format": "json",
            "lang": "Chn"
        }
        res = self._http_post(url, data=data)
        if not res or res.status_code != 200:
            return False

        if not res.text or not res.text.strip() or res.text.strip() in ("-1", "[]", "null"):
            return False

        stripped_text = res.text.strip()
        if not (stripped_text.startswith("[") or stripped_text.startswith("{")):
            logger.debug(f"[LocalSubDownloader] 射手API返回了非JSON响应内容: {stripped_text[:100]}")
            return False

        try:
            items = res.json()
            if not isinstance(items, list):
                return False

            success_any = False
            for item in items:
                files = item.get("Files")
                if not files:
                    continue
                for file_info in files:
                    ext = file_info.get("Ext", "srt")
                    link = file_info.get("Link")
                    if not link:
                        continue
                    
                    self.add_log(f"射手网 (精准Hash) 匹配到可用字幕，开始下载...")
                    dl_res = self._http_get(link)
                    if dl_res and dl_res.status_code == 200:
                        success_any |= self.save_subtitle_stream(
                            video_path=video_path,
                            filename=f"shooter_sub.{ext}",
                            content=dl_res.content,
                            existing_md5s=existing_md5s,
                            source_label="Shooter"
                        )
            return success_any
        except Exception as e:
            logger.debug(f"[LocalSubDownloader] 解析射手API响应失败: {e}")
            return False

    def download_from_xunlei(self, video_path: Path, cid: str, existing_md5s: set) -> bool:
        url = f"http://sub.xunlei.com/sub/api/subtitle?cid={cid}"
        res = self._http_get(url)
        if not res or res.status_code != 200:
            return False

        if not res.text or not res.text.strip():
            return False

        stripped_text = res.text.strip()
        if not (stripped_text.startswith("[") or stripped_text.startswith("{")):
            logger.debug(f"[LocalSubDownloader] 迅雷API返回了非JSON响应内容: {stripped_text[:100]}")
            return False

        try:
            data = res.json()
            sublist = data.get("sublist")
            if not sublist:
                return False

            success_any = False
            for sub_item in sublist:
                sname = sub_item.get("sname", "")
                surl = sub_item.get("surl")
                language = sub_item.get("language", "").lower()
                if not surl:
                    continue

                is_chinese = False
                if self._only_chinese:
                    chinese_keywords = ["zh", "cn", "chi", "chs", "cht", "双语", "中文", "简", "繁", "国语"]
                    if any(kw in language or kw in sname.lower() for kw in chinese_keywords):
                        is_chinese = True
                else:
                    is_chinese = True

                if not is_chinese:
                    continue

                self.add_log(f"迅雷字幕 (精准CID) 匹配到可用字幕，开始下载...")
                dl_res = self._http_get(surl)
                if dl_res and dl_res.status_code == 200:
                    success_any |= self.save_subtitle_stream(
                        video_path=video_path,
                        filename=sname,
                        content=dl_res.content,
                        existing_md5s=existing_md5s,
                        source_label="Xunlei"
                    )
            return success_any
        except Exception as e:
            logger.debug(f"[LocalSubDownloader] 解析迅雷API响应失败: {e}")
            return False

    def download_from_assrt(self, video_path: Path, existing_md5s: set) -> bool:
        # 优先通过 token 和 shooter hash 精准检索
        shooter_hash = self.compute_shooter_hash(video_path)
        if shooter_hash:
            url_hash = f"https://api.assrt.net/v1/sub/search?token={self._assrt_token}&filehash={shooter_hash}"
            res = self._http_get(url_hash)
            if res and res.status_code == 200:
                if res.text and res.text.strip() and (res.text.strip().startswith("[") or res.text.strip().startswith("{")):
                    try:
                        data = res.json()
                        if data.get("status") == 0:
                            subs = data.get("sub", {}).get("subs", [])
                            if subs:
                                self.add_log(f"ASSRT (精准Hash) 匹配到可用字幕，开始下载...")
                                return self._download_assrt_subs(video_path, subs[:2], existing_md5s)
                    except Exception:
                        pass

        # 降级为关键字检索评分对齐
        import re
        import urllib.parse
        keyword = video_path.stem
        tv_match = re.search(r'^(.+?)\s*-\s*(S\d+E\d+)', keyword, re.IGNORECASE)
        if tv_match:
            keyword = f"{tv_match.group(1).strip()} {tv_match.group(2).strip()}"
        else:
            keyword = re.sub(r'\s*-\s*', ' ', keyword).strip()

        url_search = f"https://api.assrt.net/v1/sub/search?token={self._assrt_token}&q={urllib.parse.quote(keyword)}&cnt=15"
        res = self._http_get(url_search)
        if not res or res.status_code != 200:
            return False

        if not res.text or not res.text.strip() or not (res.text.strip().startswith("[") or res.text.strip().startswith("{")):
            logger.debug(f"[LocalSubDownloader] ASSRT关键字检索返回了非JSON响应内容")
            return False

        try:
            data = res.json()
            if data.get("status") != 0:
                return False
            subs = data.get("sub", {}).get("subs", [])
            if not subs:
                return False

            scored_subs = []
            for sub in subs:
                filename = sub.get("filename", "")
                score = self.score_subtitle(filename, video_path.name)
                scored_subs.append((score, sub))

            scored_subs.sort(key=lambda x: x[0], reverse=True)
            top_subs = [item[1] for item in scored_subs if item[0] > 0][:2]

            if not top_subs:
                top_subs = subs[:2]

            self.add_log(f"ASSRT (智能特征打分轨) 匹配到 {len(top_subs)} 个字幕，开始下载...")
            return self._download_assrt_subs(video_path, top_subs, existing_md5s)
        except Exception as e:
            logger.debug(f"[LocalSubDownloader] 解析 ASSRT 响应失败: {e}")
            return False

    def _download_assrt_subs(self, video_path: Path, subs: list, existing_md5s: set) -> bool:
        success_any = False
        for sub in subs:
            sub_id = sub.get("id")
            filename = sub.get("filename", "assrt_sub.srt")
            if not sub_id:
                continue

            detail_url = f"https://api.assrt.net/v1/sub/detail?token={self._assrt_token}&id={sub_id}"
            res = self._http_get(detail_url)
            if not res or res.status_code != 200:
                self.add_log(f"🌐 [ASSRT] Detail接口请求失败 (id={sub_id}, status={getattr(res, 'status_code', 'N/A')})")
                continue

            if not res.text or not res.text.strip() or not (res.text.strip().startswith("[") or res.text.strip().startswith("{")):
                self.add_log(f"🌐 [ASSRT] Detail接口返回非JSON响应: {res.text[:80] if res.text else 'empty'}")
                continue

            try:
                data = res.json()
                if data.get("status") != 0:
                    self.add_log(f"🌐 [ASSRT] Detail接口状态异常: status={data.get('status')}, msg={data.get('msg', '')}")
                    continue

                # 正确路径: sub.subs[0].url (参照 ChineseSubFinder OneSubDetail 结构体)
                sub_subs = data.get("sub", {}).get("subs", [])
                if not sub_subs:
                    self.add_log(f"🌐 [ASSRT] Detail接口返回 subs 为空 (id={sub_id})")
                    continue

                url = sub_subs[0].get("url") if isinstance(sub_subs, list) else None
                if not url:
                    self.add_log(f"🌐 [ASSRT] Detail接口 subs[0].url 为空 (id={sub_id}), keys={list(sub_subs[0].keys()) if sub_subs else []}")
                    continue

                # ASSRT 文件服务器需要 Referer + UA 头，否则拒绝下载
                assrt_dl_headers = {
                    "Referer": "https://assrt.net/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                dl_res = self._http_get(url, headers=assrt_dl_headers)
                if dl_res and dl_res.status_code == 200:
                    success_any |= self.save_subtitle_stream(
                        video_path=video_path,
                        filename=filename,
                        content=dl_res.content,
                        existing_md5s=existing_md5s,
                        source_label="ASSRT"
                    )
                else:
                    # 退化：直接用 urllib 带头下载
                    try:
                        import urllib.request
                        req = urllib.request.Request(url, headers=assrt_dl_headers)
                        with urllib.request.urlopen(req, timeout=30) as resp:
                            raw = resp.read()
                        if raw:
                            success_any |= self.save_subtitle_stream(
                                video_path=video_path,
                                filename=filename,
                                content=raw,
                                existing_md5s=existing_md5s,
                                source_label="ASSRT"
                            )
                        else:
                            self.add_log(f"🌐 [ASSRT] 字幕文件内容为空: {url[:80]}")
                    except Exception as dl_e:
                        self.add_log(f"🌐 [ASSRT] 字幕文件下载失败: {dl_e} | URL: {url[:80]}")
            except Exception as e:
                self.add_log(f"🌐 [ASSRT] 解析Detail响应出错: {e}")
        return success_any

    def download_from_subdl(self, video_path: Path, existing_md5s: set) -> bool:
        import re
        keyword = video_path.stem
        tv_match = re.search(r'^(.+?)\s*-\s*(S\d+E\d+)', keyword, re.IGNORECASE)
        if tv_match:
            keyword = f"{tv_match.group(1).strip()} {tv_match.group(2).strip()}"
        else:
            keyword = re.sub(r'\s*-\s*', ' ', keyword).strip()

        url = "https://api.subdl.com/api/v1/subtitles"
        params = {
            "api_key": self._subdl_api_key,
            "film_name": keyword,
            "languages": "zh"
        }
        res = self._http_get(url, params=params)
        if not res or res.status_code != 200:
            return False

        if not res.text or not res.text.strip() or not (res.text.strip().startswith("[") or res.text.strip().startswith("{")):
            logger.debug(f"[LocalSubDownloader] SubDL关键字检索返回了非JSON响应内容")
            return False

        try:
            data = res.json()
            if not data.get("status"):
                return False
            subtitles = data.get("subtitles", [])
            if not subtitles:
                return False

            scored_subs = []
            for sub in subtitles:
                release_name = sub.get("release_name", "")
                score = self.score_subtitle(release_name, video_path.name)
                scored_subs.append((score, sub))

            scored_subs.sort(key=lambda x: x[0], reverse=True)
            top_subs = [item[1] for item in scored_subs if item[0] > 0][:2]
            if not top_subs:
                top_subs = subtitles[:2]

            success_any = False
            for sub in top_subs:
                url_dl = sub.get("url")
                release_name = sub.get("release_name", "subdl_sub.srt")
                if url_dl:
                    self.add_log(f"SubDL (智能打分轨) 选中最佳评分字幕，开始下载...")
                    dl_res = self._http_get(url_dl)
                    if dl_res and dl_res.status_code == 200:
                        success_any |= self.save_subtitle_stream(
                            video_path=video_path,
                            filename=release_name,
                            content=dl_res.content,
                            existing_md5s=existing_md5s,
                            source_label="SubDL"
                        )
            return success_any
        except Exception as e:
            logger.debug(f"[LocalSubDownloader] 解析 SubDL 响应错误: {e}")
            return False
