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

        # 初始化加载持久化日志与历史到内存，确保双保险
        try:
            self._logs_cache = self.get_data("logs") or []
            self._history_cache = self.get_data("history") or []
        except Exception:
            self._logs_cache = []
            self._history_cache = []

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
                "endpoint": self.api_manual_run,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "手动运行整理字幕",
                "description": "后台异步扫描指定目录路径，为所有视频文件进行字幕爬取",
            },
            {
                "path": "/change_root",
                "endpoint": self.api_change_root,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "切换所选根目录",
                "description": "切换当前手动字幕整理的根目录",
            },
            {
                "path": "/go_up",
                "endpoint": self.api_go_up,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "返回上一级目录",
                "description": "返回当前目录的上一层级",
            },
            {
                "path": "/go_into",
                "endpoint": self.api_go_into,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "进入子目录",
                "description": "进入当前目录的子文件夹",
            },
            {
                "path": "/run_selected",
                "endpoint": self.api_run_selected,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "为所选视频下载字幕",
                "description": "后台异步为前台选中的视频文件下载字幕",
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
            "subdl_api_key": ""
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
        return val or ""

    def get_current_dir_path(self) -> str:
        val = self.get_data("current_dir_path")
        if not val:
            val = self.get_current_root_path()
            if val:
                self.save_data("current_dir_path", val)
        return val or ""

    def get_page(self) -> List[dict]:
        """
        利用 Vuetify JSON 模式，在前台详情页渲染高颜值“手动整理控制台”、“下载历史记录表格”与“深色滚屏日志”。
        """
        # 优先从内存缓存中获取，保障前台刷新页面时无数据库锁等待延迟
        history = getattr(self, "_history_cache", None)
        if history is None:
            history = self.get_data("history") or []

        logs = getattr(self, "_logs_cache", None)
        if logs is None:
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
                        if item.is_dir():
                            sub_dirs.append(item.name)
                        elif item.is_file() and item.suffix.lower() in video_extensions:
                            video_files.append(item)
                except Exception as e:
                    logger.error(f"[LocalSubDownloader] 扫描目录 {current_dir} 失败: {e}")

            sub_dirs.sort()
            video_files.sort(key=lambda x: x.name)

        # 根目录下拉选项
        root_items = [{"title": str(p), "value": str(p)} for p in root_paths]

        # 构造子目录快捷导航按钮
        dir_buttons = []
        if sub_dirs:
            for d in sub_dirs:
                dir_buttons.append({
                    'component': 'VCol',
                    'props': {'cols': 12, 'sm': 4, 'md': 3},
                    'content': [
                        {
                            'component': 'VBtn',
                            'text': d,
                            'props': {
                                'color': 'indigo-lighten-4',
                                'variant': 'tonal',
                                'block': True,
                                'prepend-icon': 'mdi-folder',
                                'class': 'text-none justify-start text-truncate'
                            },
                            'events': {
                                'click': {
                                    'api': 'plugin/LocalSubDownloader/go_into',
                                    'method': 'post',
                                    'data': {'dir_name': d}
                                }
                            }
                        }
                    ]
                })
        else:
            dir_buttons.append({
                'component': 'VCol',
                'props': {'cols': 12},
                'content': [
                    {
                        'component': 'VListItem',
                        'props': {
                            'title': '（当前目录下无子文件夹）',
                            'class': 'text-grey'
                        }
                    }
                ]
            })

        # 构造视频选择及执行部分
        video_action_component = []
        if video_files:
            video_items = [{"title": f"🎬 {v.name}", "value": str(v)} for v in video_files]
            video_action_component = [
                {
                    'component': 'VRow',
                    'props': {'class': 'mt-2'},
                    'content': [
                        {
                            'component': 'VCol',
                            'props': {'cols': 12, 'md': 9},
                            'content': [
                                {
                                    'component': 'VAutocomplete',
                                    'props': {
                                        'model': 'selected_videos',
                                        'label': '请勾选需要下载字幕的视频文件 (支持多选)',
                                        'items': video_items,
                                        'multiple': True,
                                        'chips': True,
                                        'closable-chips': True,
                                        'clearable': True,
                                        'variant': 'outlined',
                                        'density': 'comfortable'
                                    }
                                }
                            ]
                        },
                        {
                            'component': 'VCol',
                            'props': {'cols': 12, 'md': 3, 'class': 'd-flex align-center'},
                            'content': [
                                {
                                    'component': 'VBtn',
                                    'text': '🔥 立即开始整理',
                                    'props': {
                                        'color': 'success',
                                        'block': True,
                                        'size': 'large',
                                        'prepend-icon': 'mdi-cloud-download'
                                    },
                                    'events': {
                                        'click': {
                                            'api': 'plugin/LocalSubDownloader/run_selected',
                                            'method': 'post',
                                            'data': {'videos': '{{selected_videos}}'}
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
            {
                'component': 'VCard',
                'props': {'title': '🛠️ 手动字幕整理控制台', 'variant': 'outlined', 'class': 'mb-4'},
                'content': [
                    {
                        'component': 'VCardText',
                        'content': [
                            # 1. 根目录选择与切换
                            {
                                'component': 'VRow',
                                'content': [
                                    {
                                        'component': 'VCol',
                                        'props': {'cols': 12, 'md': 9},
                                        'content': [
                                            {
                                                'component': 'VSelect',
                                                'props': {
                                                    'model': 'root_path',
                                                    'value': current_root,
                                                    'label': '媒体整理根路径 (MoviePilot配置的所有整理后路径)',
                                                    'items': root_items,
                                                    'variant': 'outlined',
                                                    'density': 'comfortable'
                                                }
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VCol',
                                        'props': {'cols': 12, 'md': 3, 'class': 'd-flex align-center'},
                                        'content': [
                                            {
                                                'component': 'VBtn',
                                                'text': '切换根目录',
                                                'props': {
                                                    'color': 'primary',
                                                    'block': True,
                                                    'size': 'large',
                                                    'prepend-icon': 'mdi-folder-swap'
                                                },
                                                'events': {
                                                    'click': {
                                                        'api': 'plugin/LocalSubDownloader/change_root',
                                                        'method': 'post',
                                                        'data': {'root_path': '{{root_path}}'}
                                                    }
                                                }
                                            }
                                        ]
                                    }
                                ]
                            },
                            # 2. 当前路径位置与导航面包屑
                            {
                                'component': 'VRow',
                                'props': {'class': 'align-center mb-2'},
                                'content': [
                                    {
                                        'component': 'VCol',
                                        'props': {'cols': 12, 'md': 9},
                                        'content': [
                                            {
                                                'component': 'VAlert',
                                                'props': {
                                                    'type': 'info',
                                                    'variant': 'tonal',
                                                    'icon': 'mdi-folder-open',
                                                    'text': f"当前目录: {current_dir or '未选择'}",
                                                    'density': 'compact'
                                                }
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VCol',
                                        'props': {'cols': 12, 'md': 3},
                                        'content': [
                                            {
                                                'component': 'VBtn',
                                                'text': '返回上一级',
                                                'props': {
                                                    'variant': 'outlined',
                                                    'color': 'secondary',
                                                    'block': True,
                                                    'prepend-icon': 'mdi-arrow-up',
                                                    'disabled': not current_dir or current_dir == current_root
                                                },
                                                'events': {
                                                    'click': {
                                                        'api': 'plugin/LocalSubDownloader/go_up',
                                                        'method': 'post'
                                                    }
                                                }
                                            }
                                        ]
                                    }
                                ]
                            },
                            # 3. 逐级子目录列表
                            {
                                'component': 'VCard',
                                'props': {
                                    'title': '📁 子目录列表 (可点击进入下一级)',
                                    'variant': 'tonal',
                                    'class': 'mb-4 bg-grey-lighten-4'
                                },
                                'content': [
                                    {
                                        'component': 'VCardText',
                                        'content': [
                                            {
                                                'component': 'VRow',
                                                'props': {'dense': True},
                                                'content': dir_buttons
                                            }
                                        ]
                                    }
                                ]
                            },
                            # 4. 视频列表与字幕爬取执行
                            *video_action_component
                        ]
                    }
                ]
            },
            {
                'component': 'VCard',
                'props': {'title': '📜 历史字幕下载记录', 'variant': 'outlined', 'class': 'mb-4'},
                'content': [
                    {
                        'component': 'VCardText',
                        'content': [
                            {
                                'component': 'VDataTable',
                                'props': {
                                    'headers': [
                                        {'title': '#', 'key': 'index', 'width': '50px'},
                                        {'title': '时间', 'key': 'time', 'width': '180px'},
                                        {'title': '视频文件', 'key': 'video'},
                                        {'title': '字幕来源', 'key': 'source', 'width': '110px'},
                                        {'title': '保存字幕名', 'key': 'file'},
                                        {'title': '下载状态', 'key': 'status', 'width': '100px'}
                                    ],
                                    'items': list(reversed(history_rows)),
                                    'density': 'compact',
                                    'items-per-page': 10
                                }
                            }
                        ]
                    }
                ]
            },
            {
                'component': 'VCard',
                'props': {'title': '💻 实时运行日志', 'variant': 'outlined'},
                'content': [
                    {
                        'component': 'VCardText',
                        'content': [
                            {
                                'component': 'VAlert',
                                'props': {
                                    'type': 'warning',
                                    'variant': 'tonal',
                                    'text': '💡 提示：因为 MoviePilot 插件页面为静态渲染，在点击“立即开始整理”后，请手动刷新浏览器网页以刷新拉取并展现最新执行进度！',
                                    'class': 'mb-3',
                                    'density': 'compact'
                                }
                            },
                            {
                                'component': 'VList',
                                'props': {
                                    'density': 'compact',
                                    'class': 'bg-grey-darken-4 text-green-accent-3 rounded',
                                    'style': 'max-height: 250px; overflow-y: auto; font-family: monospace;'
                                },
                                'content': [
                                    {
                                        'component': 'VListItem',
                                        'props': {'title': log}
                                    } for log in logs_display
                                ]
                            }
                        ]
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

        video_extensions = {'.mp4', '.mkv', '.avi', '.ts', '.wmv', '.mov', '.flv', '.rmvb'}
        for file_path_str in item_file_list:
            file_path = Path(file_path_str)
            if file_path.suffix.lower() in video_extensions:
                try:
                    self.add_log(f"检测到视频整理入库，开始处理字幕: {file_path.name}")
                    self.process_video(file_path)
                except Exception as e:
                    self.add_log(f"处理视频 {file_path.name} 失败: {e}")

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

    def api_manual_run(self, **kwargs) -> Any:
        """
        前台 POST 请求调用的端点
        """
        try:
            # 兼容 multipart/form-data 或 json 负载
            body = {}
            if kwargs:
                body = kwargs
            path_str = body.get("path")
            self.manual_run(path_str)
            return {"code": 0, "message": "手动整理任务已在后台启动，请查看下方实时运行日志"}
        except Exception as e:
            return {"code": 1, "message": f"启动整理失败: {e}"}

    def api_change_root(self, **kwargs) -> Any:
        """
        前台 POST 请求调用的端点：切换当前整理根目录
        """
        try:
            body = kwargs or {}
            root_path = body.get("root_path") or ""
            if root_path:
                self.save_data("current_root_path", root_path)
                self.save_data("current_dir_path", root_path)
                self.add_log(f"📌 手动整理根目录已切换为: {root_path}")
                return {"code": 0, "message": f"根目录已成功切换为: {root_path}"}
            return {"code": 1, "message": "切换根目录失败：接收到的路径为空"}
        except Exception as e:
            return {"code": 1, "message": f"切换根目录失败: {e}"}

    def api_go_up(self, **kwargs) -> Any:
        """
        前台 POST 请求调用的端点：返回上一级目录
        """
        try:
            current_dir = self.get_current_dir_path()
            if not current_dir:
                return {"code": 1, "message": "当前浏览路径为空"}
            
            path = Path(current_dir)
            parent_path = path.parent
            root_path = self.get_current_root_path()
            
            # 限制返回上一级时不能超出设定的根目录
            if root_path and not str(parent_path).startswith(root_path):
                return {"code": 1, "message": "已到达当前所选根目录的最顶层，无法继续返回上一级"}
                
            self.save_data("current_dir_path", str(parent_path))
            self.add_log(f"📁 已返回上一级目录: {parent_path}")
            return {"code": 0, "message": f"已成功返回上一级: {parent_path}"}
        except Exception as e:
            return {"code": 1, "message": f"返回上一级失败: {e}"}

    def api_go_into(self, **kwargs) -> Any:
        """
        前台 POST 请求调用的端点：进入子目录
        """
        try:
            body = kwargs or {}
            dir_name = body.get("dir_name") or ""
            if not dir_name:
                return {"code": 1, "message": "目标文件夹名称为空"}
                
            current_dir = self.get_current_dir_path()
            if not current_dir:
                return {"code": 1, "message": "当前浏览路径为空"}
                
            next_path = Path(current_dir) / dir_name
            if next_path.exists() and next_path.is_dir():
                self.save_data("current_dir_path", str(next_path))
                self.add_log(f"📁 已进入子目录: {dir_name}")
                return {"code": 0, "message": f"已成功进入目录: {dir_name}"}
            return {"code": 1, "message": "目标文件夹不存在或不是目录"}
        except Exception as e:
            return {"code": 1, "message": f"进入子目录失败: {e}"}

    def api_run_selected(self, **kwargs) -> Any:
        """
        前台 POST 请求调用的端点：批量整理选中的视频字幕
        """
        try:
            body = kwargs or {}
            videos = body.get("videos")
            if not videos:
                return {"code": 1, "message": "请先勾选需要下载字幕的视频文件！"}
                
            video_list = []
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
                    
            if not video_list:
                return {"code": 1, "message": "未能解析出有效的视频文件路径"}
                
            # 开启异步后台线程下载，规避超时
            thread = threading.Thread(target=self._process_selected_videos, args=(video_list,))
            thread.daemon = True
            thread.start()
            
            return {"code": 0, "message": f"已成功启动 {len(video_list)} 个视频的字幕下载任务，请在下方观察实时运行日志"}
        except Exception as e:
            return {"code": 1, "message": f"启动批量字幕下载失败: {e}"}

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

        # 1. 收集本地已有字幕 MD5 用于去重
        existing_md5s = set()
        for sub_file in video_path.parent.glob(f"{video_path.stem}*"):
            if sub_file.suffix.lower() in {'.srt', '.ass', '.vtt'}:
                try:
                    with open(sub_file, 'rb') as sf:
                        data = sf.read()
                    existing_md5s.add(hashlib.md5(data).hexdigest())
                except Exception:
                    pass

        # 2. 精准匹配轨道 (Hash-based)
        precision_success = False

        # 2.1 射手网精准匹配
        if self._shooter_enabled:
            try:
                shooter_hash = self.compute_shooter_hash(video_path)
                if shooter_hash:
                    precision_success |= self.download_from_shooter(video_path, shooter_hash, existing_md5s)
            except Exception as e:
                logger.error(f"[LocalSubDownloader] 射手哈希匹配报错: {e}")

        # 2.2 迅雷精准匹配
        if self._xunlei_enabled:
            try:
                xunlei_cid = self.compute_xunlei_cid(video_path)
                if xunlei_cid:
                    precision_success |= self.download_from_xunlei(video_path, xunlei_cid, existing_md5s)
            except Exception as e:
                logger.error(f"[LocalSubDownloader] 迅雷哈希匹配报错: {e}")

        # 3. 智能模糊评分检索轨道 (仅在精准轨道毫无所获且本地无字幕时触发)
        if not precision_success and len(existing_md5s) == 0:
            # 3.1 ASSRT 评分检索
            if self._assrt_enabled and self._assrt_token:
                try:
                    precision_success |= self.download_from_assrt(video_path, existing_md5s)
                except Exception as e:
                    logger.error(f"[LocalSubDownloader] ASSRT评分检索报错: {e}")

            # 3.2 A4k (SubDL) 评分检索
            if self._subdl_enabled and self._subdl_api_key:
                try:
                    precision_success |= self.download_from_subdl(video_path, existing_md5s)
                except Exception as e:
                    logger.error(f"[LocalSubDownloader] SubDL评分检索报错: {e}")

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
                    class FakeResponse:
                        def __init__(self, code, content):
                            self.status_code = code
                            self.content = content
                            self.text = content.decode('utf-8', errors='ignore')
                        def json(self):
                            return json.loads(self.text)
                    return FakeResponse(response.getcode(), response.read())
            except Exception as e:
                logger.error(f"[LocalSubDownloader] POST 退化调用失败: {e}")
                return None

    def _http_get(self, url: str, headers: dict = None, params: dict = None) -> Any:
        try:
            from app.utils.http import RequestUtils
            res = RequestUtils(headers=headers).get(url, params=params)
            return res
        except Exception:
            import urllib.request
            import urllib.parse
            if params:
                url = url + "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers=headers or {}, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    class FakeResponse:
                        def __init__(self, code, content):
                            self.status_code = code
                            self.content = content
                            self.text = content.decode('utf-8', errors='ignore')
                        def json(self):
                            return json.loads(self.text)
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
            logger.error(f"[LocalSubDownloader] 解析射手API响应失败: {e}")
            return False

    def download_from_xunlei(self, video_path: Path, cid: str, existing_md5s: set) -> bool:
        url = f"http://sub.xunlei.com/sub/api/subtitle?cid={cid}"
        res = self._http_get(url)
        if not res or res.status_code != 200:
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
            logger.error(f"[LocalSubDownloader] 解析迅雷API响应失败: {e}")
            return False

    def download_from_assrt(self, video_path: Path, existing_md5s: set) -> bool:
        # 优先通过 token 和 shooter hash 精准检索
        shooter_hash = self.compute_shooter_hash(video_path)
        if shooter_hash:
            url_hash = f"https://api.assrt.net/v1/sub/search?token={self._assrt_token}&filehash={shooter_hash}"
            res = self._http_get(url_hash)
            if res and res.status_code == 200:
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
        keyword = video_path.stem
        url_search = f"https://api.assrt.net/v1/sub/search?token={self._assrt_token}&q={keyword}&cnt=15"
        res = self._http_get(url_search)
        if not res or res.status_code != 200:
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
            logger.error(f"[LocalSubDownloader] 解析 ASSRT 响应失败: {e}")
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
            if res and res.status_code == 200:
                try:
                    data = res.json()
                    if data.get("status") == 0:
                        detail = data.get("sub", {}).get("detail", {})
                        url = detail.get("url")
                        if url:
                            dl_res = self._http_get(url)
                            if dl_res and dl_res.status_code == 200:
                                success_any |= self.save_subtitle_stream(
                                    video_path=video_path,
                                    filename=filename,
                                    content=dl_res.content,
                                    existing_md5s=existing_md5s,
                                    source_label="ASSRT"
                                )
                except Exception:
                    pass
        return success_any

    def download_from_subdl(self, video_path: Path, existing_md5s: set) -> bool:
        keyword = video_path.stem
        url = "https://api.subdl.com/api/v1/subtitles"
        params = {
            "api_key": self._subdl_api_key,
            "film_name": keyword,
            "languages": "zh,zho,chi"
        }
        res = self._http_get(url, params=params)
        if not res or res.status_code != 200:
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
            logger.error(f"[LocalSubDownloader] 解析 SubDL 响应错误: {e}")
            return False
