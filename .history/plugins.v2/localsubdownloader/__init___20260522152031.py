import hashlib
import io
import json
import zipfile
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
    plugin_version = "2.0.0"
    # 插件作者
    plugin_author = "Antigravity"
    # 作者主页
    author_url = "https://github.com/Antigravity"
    # 插件配置项ID前缀
    plugin_config_prefix = "localsubdownloader_"
    # 加载顺序
    plugin_order = 6
    # 可使用的用户级别
    auth_level = 1

    # 配置变量
    _enabled = False
    _only_chinese = True
    _shooter_enabled = True
    _xunlei_enabled = True
    _assrt_enabled = False
    _assrt_token = ""
    _subdl_enabled = False
    _subdl_api_key = ""

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

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

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
        pass

    def stop_service(self):
        pass

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
                    logger.info(f"[LocalSubDownloader] 检测到视频入库，开始处理字幕: {file_path.name}")
                    self.process_video(file_path)
                except Exception as e:
                    logger.error(f"[LocalSubDownloader] 处理视频 {file_path.name} 失败: {e}")

    def process_video(self, video_path: Path):
        if not video_path.exists():
            logger.warn(f"[LocalSubDownloader] 视频文件不存在: {video_path}")
            return

        # 1. 收集本地已有字幕的 MD5 签名以进行去重
        existing_md5s = set()
        for sub_file in video_path.parent.glob(f"{video_path.stem}*"):
            if sub_file.suffix.lower() in {'.srt', '.ass', '.vtt'}:
                try:
                    with open(sub_file, 'rb') as sf:
                        data = sf.read()
                    existing_md5s.add(hashlib.md5(data).hexdigest())
                except Exception:
                    pass

        # 2. 精准匹配轨道 (Hash-based) - Shooter & Xunlei
        precision_success = False

        # 2.1 射手网匹配
        if self._shooter_enabled:
            try:
                shooter_hash = self.compute_shooter_hash(video_path)
                if shooter_hash:
                    logger.info(f"[LocalSubDownloader] 射手网计算Hash成功: {shooter_hash}，开始发起匹配")
                    precision_success |= self.download_from_shooter(video_path, shooter_hash, existing_md5s)
            except Exception as e:
                logger.error(f"[LocalSubDownloader] 射手网匹配报错: {e}")

        # 2.2 迅雷字幕匹配
        if self._xunlei_enabled:
            try:
                xunlei_cid = self.compute_xunlei_cid(video_path)
                if xunlei_cid:
                    logger.info(f"[LocalSubDownloader] 迅雷计算CID成功: {xunlei_cid}，开始发起匹配")
                    precision_success |= self.download_from_xunlei(video_path, xunlei_cid, existing_md5s)
            except Exception as e:
                logger.error(f"[LocalSubDownloader] 迅雷匹配报错: {e}")

        # 3. 智能模糊评分检索轨道 - ASSRT & A4k (SubDL)
        # 仅当精准轨道未能匹配到任何可用字幕时，才作为备用链路启用，以节省API调用资源
        if not precision_success and len(existing_md5s) == 0:
            logger.info(f"[LocalSubDownloader] 精准轨道未匹配到字幕，将退回到备用模糊评分检索轨道")
            
            # 3.1 ASSRT 模糊匹配
            if self._assrt_enabled and self._assrt_token:
                try:
                    self.download_from_assrt(video_path, existing_md5s)
                except Exception as e:
                    logger.error(f"[LocalSubDownloader] ASSRT 模糊检索报错: {e}")

            # 3.2 A4k (SubDL) 模糊匹配
            if self._subdl_enabled and self._subdl_api_key:
                try:
                    self.download_from_subdl(video_path, existing_md5s)
                except Exception as e:
                    logger.error(f"[LocalSubDownloader] SubDL 模糊检索报错: {e}")

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

    # ================= 网络请求与备用请求退化机制 =================

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

    # ================= 核心下载与智能解压、去重写入 =================

    def save_subtitle_stream(self, video_path: Path, filename: str, content: bytes, existing_md5s: set) -> bool:
        """
        保存下载的字幕流。支持内存解压 zip 压缩包，实现MD5去重过滤并按规范命名保存。
        """
        if not content:
            return False

        # 收集解包后的字幕 [(filename, bytes)]
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
                logger.error(f"[LocalSubDownloader] 解压字幕ZIP失败: {e}")
        else:
            sub_list.append((filename, content))

        success_any = False
        for idx, (name, data) in enumerate(sub_list):
            if not data:
                continue

            # 内容 MD5 去重
            sub_md5 = hashlib.md5(data).hexdigest()
            if sub_md5 in existing_md5s:
                logger.info(f"[LocalSubDownloader] 字幕内容与已有文件重复，跳过保存: {name}")
                continue

            ext = Path(name).suffix.lower() or ".srt"
            
            # 检测语言，若仅下载中文，则进行简单的关键字判断（如果是以 shooter 等获取，通常为中文字幕）
            # 也可以简单保存为 .zh-cn.srt 等
            lang_suffix = ".zh-cn"
            if "zh-tw" in name.lower() or "cht" in name.lower() or "tc" in name.lower():
                lang_suffix = ".zh-tw"
            
            # 生成标准的 MoviePilot 保存文件名
            sub_idx_str = f".{idx}" if idx > 0 else ""
            target_name = f"{video_path.stem}{lang_suffix}{sub_idx_str}{ext}"
            target_path = video_path.parent / target_name

            try:
                target_path.write_bytes(data)
                existing_md5s.add(sub_md5)
                logger.info(f"[LocalSubDownloader] 成功保存字幕到: {target_name} ({len(data)} 字节)")
                success_any = True
            except Exception as e:
                logger.error(f"[LocalSubDownloader] 保存字幕文件 {target_name} 失败: {e}")

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

    # ================= 各源具体 API 请求实现 =================

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
            logger.warn(f"[LocalSubDownloader] 射手网请求未返回成功数据")
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
                    
                    logger.info(f"[LocalSubDownloader] 射手网匹配到可用字幕，开始下载: {link}")
                    dl_res = self._http_get(link)
                    if dl_res and dl_res.status_code == 200:
                        success_any |= self.save_subtitle_stream(
                            video_path=video_path,
                            filename=f"shooter_sub.{ext}",
                            content=dl_res.content,
                            existing_md5s=existing_md5s
                        )
            return success_any
        except Exception as e:
            logger.error(f"[LocalSubDownloader] 解析射手网JSON响应失败: {e}")
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

                # 语言过滤
                is_chinese = False
                if self._only_chinese:
                    chinese_keywords = ["zh", "cn", "chi", "chs", "cht", "双语", "中文", "简", "繁", "国语"]
                    if any(kw in language or kw in sname.lower() for kw in chinese_keywords):
                        is_chinese = True
                else:
                    is_chinese = True

                if not is_chinese:
                    continue

                logger.info(f"[LocalSubDownloader] 迅雷匹配到可用字幕: {sname}，开始下载")
                dl_res = self._http_get(surl)
                if dl_res and dl_res.status_code == 200:
                    ext = Path(sname).suffix.lower() or ".srt"
                    success_any |= self.save_subtitle_stream(
                        video_path=video_path,
                        filename=sname,
                        content=dl_res.content,
                        existing_md5s=existing_md5s
                    )
            return success_any
        except Exception as e:
            logger.error(f"[LocalSubDownloader] 解析迅雷响应失败: {e}")
            return False

    def download_from_assrt(self, video_path: Path, existing_md5s: set) -> bool:
        # ASSRT 支持以 token 和 shooter hash 精准检索
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
                            logger.info(f"[LocalSubDownloader] ASSRT (Hash轨) 匹配到 {len(subs)} 个精准字幕")
                            return self._download_assrt_subs(video_path, subs[:3], existing_md5s)
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

            # 时间轴对齐特征关键字打分
            scored_subs = []
            for sub in subs:
                filename = sub.get("filename", "")
                score = self.score_subtitle(filename, video_path.name)
                scored_subs.append((score, sub))

            # 过滤并降序排序
            scored_subs.sort(key=lambda x: x[0], reverse=True)
            top_subs = [item[1] for item in scored_subs if item[0] > 0][:2]

            if not top_subs:
                # 若完全无匹配特征，选取前2个默认项
                top_subs = subs[:2]

            logger.info(f"[LocalSubDownloader] ASSRT 检索评分完毕，选取前 {len(top_subs)} 个优质字幕发起下载")
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

            # 获取详情以下载
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
                                    existing_md5s=existing_md5s
                                )
                except Exception:
                    pass
        return success_any

    def download_from_subdl(self, video_path: Path, existing_md5s: set) -> bool:
        # A4k底座: SubDL 检索
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

            # 特征对齐打分
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
                    logger.info(f"[LocalSubDownloader] SubDL (A4k轨) 下载最佳评分字幕: {release_name}")
                    dl_res = self._http_get(url_dl)
                    if dl_res and dl_res.status_code == 200:
                        success_any |= self.save_subtitle_stream(
                            video_path=video_path,
                            filename=release_name,
                            content=dl_res.content,
                            existing_md5s=existing_md5s
                        )
            return success_any
        except Exception as e:
            logger.error(f"[LocalSubDownloader] 解析 SubDL 响应错误: {e}")
            return False
