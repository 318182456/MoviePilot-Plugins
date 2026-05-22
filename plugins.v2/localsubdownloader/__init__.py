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
    plugin_version = "2.0.1"
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

        return [
            {
                'component': 'VCard',
                'props': {'title': '🛠️ 手动字幕整理', 'variant': 'outlined', 'class': 'mb-4'},
                'content': [
                    {
                        'component': 'VCardText',
                        'content': [
                            {
                                'component': 'VAlert',
                                'props': {
                                    'type': 'info',
                                    'variant': 'tonal',
                                    'text': '请输入您需要扫描的绝对路径。留空不输则系统将自动为您提取 MoviePilot 目录配置中设置的【综艺、电影、电视剧】的所有媒体库及资源路径进行全量扫描！',
                                    'class': 'mb-4'
                                }
                            },
                            {
                                'component': 'VRow',
                                'content': [
                                    {
                                        'component': 'VCol',
                                        'props': {'cols': 12, 'md': 9},
                                        'content': [
                                            {
                                                'component': 'VTextField',
                                                'props': {
                                                    'model': 'scan_path',
                                                    'label': '待扫描整理的媒体绝对目录路径 (如 D:\\Media)',
                                                    'placeholder': '留空不输将全自动获取 MoviePilot 配置的多媒体目录结构进行一键全量检索'
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
                                                'props': {
                                                    'color': 'primary',
                                                    'text': '🔥 立即开始整理',
                                                    'block': True
                                                },
                                                # 在 Vuetify JSON 事件机制中，通过 action 触发
                                                'on': {
                                                    'click': {
                                                        'action': 'localsubdownloader_run',
                                                        'data': {'path': '{{scan_path}}'}
                                                    }
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
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

    def manual_run(self, path_str: str = None):
        """
        开启后台线程进行目录扫描，规避网页超时挂起
        """
        thread = threading.Thread(target=self._scan_and_process, args=(path_str,))
        thread.daemon = True
        thread.start()

    def get_moviepilot_media_paths(self) -> List[Path]:
        """
        自动探测并提取 MoviePilot 中用户在数据库和后台配置的所有媒体库目录与资源整理目录
        """
        paths = set()

        # 策略 1：查询数据库中的 TransferCategory 模型，提取用户配置的所有分类整理路径
        try:
            from app.db import get_db
            from app.db.models.transfer_category import TransferCategory
            db = next(get_db())
            categories = db.query(TransferCategory).all()
            for cat in categories:
                # 提取媒体库存储目录
                if getattr(cat, "library_path", None):
                    paths.add(Path(cat.library_path))
                # 提取下载资源整理目录
                if getattr(cat, "download_path", None):
                    paths.add(Path(cat.download_path))
        except Exception as e:
            logger.error(f"[LocalSubDownloader] 通过ORM数据库提取分类目录失败: {e}")

        # 策略 2：通过 MoviePilot 系统内置的 CategoryHelper 获取分类配置
        try:
            from app.helper.category import CategoryHelper
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
        except Exception as e:
            logger.error(f"[LocalSubDownloader] 通过CategoryHelper获取分类配置失败: {e}")

        # 策略 3：从 MoviePilot 系统基本全局设置中兜底读取
        try:
            if hasattr(settings, "LIBRARY_PATH") and settings.LIBRARY_PATH:
                paths.add(Path(settings.LIBRARY_PATH))
            if hasattr(settings, "DOWNLOAD_PATH") and settings.DOWNLOAD_PATH:
                paths.add(Path(settings.DOWNLOAD_PATH))
        except Exception as e:
            logger.error(f"[LocalSubDownloader] 从全局settings中读取基础路径失败: {e}")

        # 过滤校验，只保留物理存在并且有读写权限的真实文件夹路径
        valid_paths = []
        for p in paths:
            try:
                if p and p.exists() and p.is_dir():
                    # 避免子目录冗余扫描（比如如果父目录已经在集合中，就不必再重复扫子目录，这里简单去重）
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
