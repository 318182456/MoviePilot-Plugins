import os
import pathlib
from typing import List, Tuple, Dict, Any

from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase


class enveditor(_PluginBase):
    plugin_name = "环境变量编辑器"
    plugin_desc = "在线查看与编辑 app.env 配置文件，支持即时热更新当前运行进程的系统环境变量及配置对象。"
    plugin_icon = "settings.png"
    plugin_version = "1.0.0"
    plugin_author = "318182456"
    author_url = "https://github.com/318182456"
    plugin_config_prefix = "enveditor_"
    plugin_order = 10
    auth_level = 1

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._env_filepath = (config.get("env_filepath") or "/config/app.env").strip()
            self._env_content = config.get("env_content") or ""
            
            # 当用户在设置表单中点击保存时，自动将内容写入文件并同步到系统内存
            if self._enabled and self._env_content:
                try:
                    self.save_and_apply_env(self._env_filepath, self._env_content)
                except Exception as e:
                    logger.error(f"[EnvEditor] 保存并应用环境变量失败: {e}")

    def stop_service(self):
        """
        停止服务，实现基类的生命周期抽象方法
        """
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        """
        实现基类的抽象方法，由于已切回 Form 页，无需额外自定义 API
        """
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        # 动态加载 app.env 文件的当前内容
        filepath = self.get_data("env_filepath") or "/config/app.env"
        env_content = ""
        
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    env_content = f.read()
            except Exception as e:
                env_content = f"# 读取文件失败: {e}"
                logger.error(f"[EnvEditor] 读取环境变量文件失败: {e}")
        else:
            env_content = f"# 未找到配置文件: {filepath}\n# 请确认路径是否正确，或者在下方框中直接编写内容，点击保存来新建文件。"

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
                                'props': {'cols': 12, 'md': 8},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'env_filepath',
                                            'label': 'app.env 配置文件绝对路径',
                                            'hint': 'Docker 容器中一般为 /config/app.env。若不存在，保存时将自动创建。'
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
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'env_content',
                                            'label': 'app.env 原始内容编辑',
                                            'rows': 18,
                                            'hint': '每行一个 KEY=VALUE。支持 # 开头的注释行。点击底部的“保存”按钮即可回写文件并热应用到内存！',
                                            'persistent-hint': True
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
            "env_filepath": filepath,
            "env_content": env_content
        }

    def get_state(self) -> bool:
        return self._enabled

    def get_page(self) -> List[dict]:
        pass

    def save_and_apply_env(self, filepath: str, content: str):
        """
        保存环境变量至文件，并应用至内存 (os.environ & settings)。
        """
        path = pathlib.Path(filepath)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"[EnvEditor] 环境变量成功写入文件: {filepath}")
            
            # 同时持久化到插件的内置存储中，保证下次加载页面时能够读取到最新修改
            self.save_data("env_filepath", filepath)
            self.save_data("env_content", content)
        except Exception as e:
            logger.error(f"[EnvEditor] 写入配置文件失败: {e}")
            raise e

        # 解析配置并应用到 os.environ 与 settings
        lines = content.splitlines()
        updated_count = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            
            if "=" in line:
                parts = line.split("=", 1)
                key = parts[0].strip()
                value = parts[1].strip()
                
                if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                
                os.environ[key] = value
                
                if hasattr(settings, key):
                    orig_type = type(getattr(settings, key))
                    try:
                        if orig_type is bool:
                            typed_val = value.lower() in ("true", "1", "yes", "on")
                        elif orig_type is int:
                            typed_val = int(value)
                        elif orig_type is float:
                            typed_val = float(value)
                        elif orig_type is list:
                            typed_val = [x.strip() for x in value.split(",") if x.strip()]
                        else:
                            typed_val = value
                        
                        setattr(settings, key, typed_val)
                        logger.info(f"[EnvEditor] 热更新配置成功: {key} = {typed_val}")
                    except Exception as ex:
                        logger.warning(f"[EnvEditor] 类型转换热更新 {key} 失败: {ex}")
                
                updated_count += 1
                
        logger.info(f"[EnvEditor] 环境热更新完成，共更新 {updated_count} 个变量。")
