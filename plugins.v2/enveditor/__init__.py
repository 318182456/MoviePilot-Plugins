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
                                            'rows': 16,
                                            'auto-grow': False,
                                            'hint': '每行一个 KEY=VALUE。支持 # 开头的注释行。点击底部的“保存”按钮即可回写文件并热应用到内存！',
                                            'persistent-hint': True,
                                            'style': {
                                                'font-family': 'Consolas, Monaco, "Courier New", Courier, monospace',
                                                'font-size': '14px',
                                                'line-height': '1.5',
                                                'letter-spacing': '0px',
                                                'background': '#ffffff',
                                                'color': '#333333',
                                                'padding': '12px',
                                                'border-radius': '6px',
                                                'border': '1px solid rgba(var(--v-border-color), var(--v-border-opacity))'
                                            }
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
        # 保存前进行格式校验
        lines = content.splitlines()
        line_num = 0
        for line in lines:
            line_num += 1
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            if "=" not in line_str:
                raise ValueError(f"格式错误（第 {line_num} 行）: 每一行有效的环境变量必须为 KEY=VALUE 格式。错误行内容: '{line_str}'")
            parts = line_str.split("=", 1)
            key = parts[0].strip()
            if not key:
                raise ValueError(f"格式错误（第 {line_num} 行）: 变量名不能为空。错误行内容: '{line_str}'")
            if not (key.replace("_", "").isalnum() or key.replace("_", "").replace("-", "").isalnum()):
                raise ValueError(f"格式错误（第 {line_num} 行）: 变量名 '{key}' 包含非法字符，应只包含字母、数字、下划线或连字符。")

        path = pathlib.Path(filepath)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"[EnvEditor] 环境变量成功写入文件: {filepath}")
        except Exception as e:
            logger.error(f"[EnvEditor] 写入配置文件失败: {e}")
            raise e
        # 1. 收集本次提交中所有有效的环境变量 KEY
        current_keys = set()
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key = line.split("=", 1)[0].strip()
                if key:
                    current_keys.add(key)

        # 2. 检查被删除的变量，并从内存与 settings 中注销
        # 我们需要从原先保存在插件内置数据中的 env_content 提取上一次的 KEY，从而找出哪些 KEY 被删除了
        prev_content = self.get_data("env_content") or ""
        prev_keys = set()
        for line in prev_content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key = line.split("=", 1)[0].strip()
                if key:
                    prev_keys.add(key)
        
        deleted_keys = prev_keys - current_keys
        deleted_count = 0
        for key in deleted_keys:
            # 从 os.environ 中移除
            if key in os.environ:
                os.environ.pop(key, None)
                logger.info(f"[EnvEditor] 从环境变量（os.environ）中注销已删除的变量: {key}")
            
            # 如果 settings 中也包含了这个配置，则需要将其进行重置/还原
            if hasattr(settings, key):
                try:
                    # 尝试通过读取 settings 类本身的默认值属性来做重置，若无默认值则赋为 None
                    default_val = getattr(settings.__class__, key, None)
                    # 额外处理某些类型，如果是实例属性且未定义类属性默认值，则降级赋为 None 或空值
                    if default_val is None:
                        # 尝试通过初始 settings 实例获取一个相对安全的默认值，通常核心配置未被赋值时是 None 或 False
                        default_val = None
                    setattr(settings, key, default_val)
                    logger.info(f"[EnvEditor] 将核心配置 settings 中的 {key} 重置为默认值: {default_val}")
                except Exception as ex:
                    logger.warning(f"[EnvEditor] 重置 settings 中的已删除变量 {key} 失败: {ex}")
            deleted_count += 1

        # 同时持久化到插件的内置存储中，保证下次加载页面时能够读取到最新修改
        try:
            self.save_data("env_filepath", filepath)
            self.save_data("env_content", content)
        except Exception as e:
            logger.error(f"[EnvEditor] 保存持久化数据失败: {e}")

        # 3. 解析配置并增量应用到 os.environ 与 settings
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
                
                # 脏数据检查：如果该环境变量已存在于 os.environ 中且值完全相同，则跳过不更新
                if os.environ.get(key) == value:
                    continue

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
                        
                        # 检查 settings 内的当前值是否与新类型值相同，不同才热更新
                        if getattr(settings, key) != typed_val:
                            setattr(settings, key, typed_val)
                            logger.info(f"[EnvEditor] 增量热更新配置成功: {key} = {typed_val}")
                    except Exception as ex:
                        logger.warning(f"[EnvEditor] 类型转换热更新 {key} 失败: {ex}")
                else:
                    logger.info(f"[EnvEditor] 增量热更新环境变量成功: {key} = {value}")
                
                updated_count += 1
                
        logger.info(f"[EnvEditor] 环境增量热更新完成。新增/修改: {updated_count} 个，删除/重置: {deleted_count} 个。")
