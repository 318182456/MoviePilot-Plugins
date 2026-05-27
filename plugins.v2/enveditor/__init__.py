import os
import pathlib
import time
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
            format_to_template = config.get("format_to_template", False)
            
            # 当用户在设置表单中点击保存时，自动将内容写入文件并同步到系统内存
            if self._enabled and self._env_content:
                try:
                    # 获取格式化后的最终文件内容
                    final_content = self.save_and_apply_env(
                        self._env_filepath, 
                        self._env_content, 
                        format_to_template
                    )
                    
                    # 关键修改：重置一次性模板开关为 False，并将格式化后的最新内容写回系统插件配置数据库
                    config["format_to_template"] = False
                    
                    # 彻底消除系统冗余缓存：不把庞大且会产生引号变异的环境变量内容冗余保存在 SQLite 数据库中。
                    # 磁盘的 app.env 才是其唯一的物理实体。在此将其直接从保存的 config 字典中剔除！
                    config.pop("env_content", None)
                    config.pop("env_history_display", None)
                    self.update_config(config)
                    
                    # 重新拉取同步插件内部缓存
                    self.save_data("env_content", final_content)
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
        # 实时获取最新的 app.env 文件绝对路径
        filepath = getattr(self, "_env_filepath", None)
        if not filepath:
            filepath = self.get_data("env_filepath") or "/config/app.env"
        
        env_content = ""
        # 每次加载表单时，强制从磁盘文件重新读取最新内容，确保数据同步
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    env_content = f.read()
                # 重新同步保存一份到内置数据库
                self.save_data("env_content", env_content)
                # 彻底消除冗余数据库缓存：从系统内置 of config 数据库中移除过时的 env_content 与 env_history_display。
                # 这样可以保证每次加载页面时，系统合并的都是从物理磁盘读取的最真实的 env_content 内容！
                config = self.get_config() or {}
                if "env_content" in config or "env_history_display" in config:
                    config.pop("env_content", None)
                    config.pop("env_history_display", None)
                    self.update_config(config)
            except Exception as e:
                env_content = f"# 读取文件失败: {e}"
                logger.error(f"[EnvEditor] 读取环境变量文件失败: {e}")
        else:
            env_content = f"# 未找到配置文件: {filepath}\n# 请确认路径是否正确，或者在下方框中直接编写内容，点击保存来新建文件。"

        # 加载并生成配置的修改履历（仅展示改变的字段）
        history_list = self.get_data("env_history") or []
        history_hint = "暂无历史保存履历。"
        if history_list:
            hints = []
            for idx, record in enumerate(reversed(history_list[-8:])): # 显示最近8次
                changes_detail = ""
                changes = record.get("changes") or []
                if changes:
                    changes_detail = "\n".join([f"   ├─ {c}" for c in changes])
                else:
                    changes_detail = "   ├─ 无发生变化的配置项。"
                
                hints.append(
                    f"⏰ 【变更 #{len(history_list) - idx}】 保存时间: {record.get('time')}\n"
                    f"{changes_detail}"
                )
            history_hint = "\n======================================================================\n".join(hints)

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
                                            'model': 'format_to_template',
                                            'label': '模板化格式化并排版（对齐与注释说明）',
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
                                            'model': 'env_filepath',
                                            'label': 'app.env 配置文件路径',
                                            'hint': '默认 /config/app.env'
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
                                            'rows': 14,
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
                                            'model': 'env_history_display',
                                            'label': '📜 变量值历史变更履历（仅展示被修改的字段与具体旧值）',
                                            'rows': 6,
                                            'readonly': True,
                                            'style': {
                                                'font-family': 'Consolas, Monaco, "Courier New", Courier, monospace',
                                                'font-size': '12px',
                                                'background': '#f8f9fa',
                                                'color': '#666666',
                                                'padding': '12px',
                                                'border-radius': '6px',
                                                'border': '1px dotted #ccc'
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
            "format_to_template": False,
            "env_filepath": filepath,
            "env_content": env_content,
            "env_history_display": history_hint
        }

    def get_state(self) -> bool:
        return self._enabled

    def get_page(self) -> List[dict]:
        pass

    def save_and_apply_env(self, filepath: str, content: str, format_to_template: bool = False) -> str:
        """
        保存并应用环境变量，支持格式化和增量热更新当前运行进程的系统环境变量及配置对象。
        """
        lines = content.splitlines()
        parsed_envs = {}
        
        # 1. 解析当前提交的内容为 dict
        for line in lines:
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            if "=" in line_str:
                parts = line_str.split("=", 1)
                k = parts[0].strip()
                v = parts[1].strip()
                # 递归去除单双引号包裹
                while (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                parsed_envs[k] = v

        # 2. 如果开启了模板格式化与对齐，动态加载 categories.json
        if format_to_template:
            import json
            categories = []
            try:
                json_path = pathlib.Path(__file__).parent / "categories.json"
                if json_path.exists():
                    with open(json_path, "r", encoding="utf-8") as f:
                        categories = json.load(f)
                else:
                    logger.warning(f"[EnvEditor] 未找到 categories.json 模板文件: {json_path}")
            except Exception as ex:
                logger.error(f"[EnvEditor] 加载 categories.json 失败: {ex}")

            formatted_lines = []
            formatted_lines.append("# ==============================================================================")
            formatted_lines.append("#                      MoviePilot 环境变量完整配置文件 (app.env)")
            formatted_lines.append("# ==============================================================================")
            formatted_lines.append("")

            used_keys = set()
            for cat in categories:
                # 找出在该分类下且在当前用户配置中存在的 KEYS
                cat_envs = {k: parsed_envs[k] for k in cat["keys"] if k in parsed_envs}
                if cat_envs:
                    formatted_lines.append(f"# ==================== {cat['title']} ====================")
                    for k, val in cat_envs.items():
                        desc = cat["keys"][k]
                        formatted_lines.append(f"# {desc}")
                        formatted_lines.append(f"{k}='{val}'")
                        used_keys.add(k)
                    formatted_lines.append("")

            # 将没有归类的其它自定义环境变量放到最后
            other_keys = set(parsed_envs.keys()) - used_keys
            if other_keys:
                formatted_lines.append("# ==================== 12. 其它自定义配置 ====================")
                for k in sorted(other_keys):
                    val = parsed_envs[k]
                    formatted_lines.append(f"{k}='{val}'")
                formatted_lines.append("")

            content = "\n".join(formatted_lines)

        # 3. 写入 app.env 文件
        path = pathlib.Path(filepath)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"[EnvEditor] 环境变量成功写入文件 (模板格式化: {format_to_template}): {filepath}")
        except Exception as e:
            logger.error(f"[EnvEditor] 写入配置文件失败: {e}")
            raise e

        # 4. 获取上一次保存的内容，识别变更项与被删除项
        prev_content = self.get_data("env_content") or ""
        prev_envs = {}
        for line in prev_content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                parts = line.split("=", 1)
                k = parts[0].strip()
                v = parts[1].strip()
                while (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                prev_envs[k] = v

        current_keys = set(parsed_envs.keys())
        prev_keys = set(prev_envs.keys())
        deleted_keys = prev_keys - current_keys
        
        changes = []
        deleted_count = 0
        
        # 5. 处理被删除的环境变量与配置
        for key in deleted_keys:
            old_val = prev_envs.get(key, "")
            changes.append(f"[删除] {key} (原值: '{old_val}')")
            if key in os.environ:
                os.environ.pop(key, None)
                logger.info(f"[EnvEditor] 从环境变量中注销已删除的变量: {key}")
            
            if hasattr(settings, key):
                try:
                    default_val = getattr(settings.__class__, key, None)
                    setattr(settings, key, default_val)
                    logger.info(f"[EnvEditor] 将核心配置 settings 中的 {key} 重置为默认值: {default_val}")
                except Exception as ex:
                    logger.warning(f"[EnvEditor] 重置 settings 中的已删除变量 {key} 失败: {ex}")
            deleted_count += 1

        # 同时持久化到插件的内置存储中
        try:
            self.save_data("env_filepath", filepath)
            self.save_data("env_content", content)
        except Exception as e:
            logger.error(f"[EnvEditor] 保存持久化数据失败: {e}")

        # 6. 处理新增和被修改的环境变量，热应用到内存与 settings
        updated_count = 0
        for key, value in parsed_envs.items():
            if os.environ.get(key) == value:
                continue

            if key in prev_envs:
                changes.append(f"[修改] {key}: '{prev_envs[key]}' -> '{value}'")
            else:
                changes.append(f"[新增] {key} = '{value}'")

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
                    
                    if getattr(settings, key) != typed_val:
                        setattr(settings, key, typed_val)
                        logger.info(f"[EnvEditor] 增量热更新配置成功: {key} = {typed_val}")
                except Exception as ex:
                    logger.warning(f"[EnvEditor] 类型转换热更新 {key} 失败: {ex}")
            else:
                logger.info(f"[EnvEditor] 增量热更新环境变量成功: {key} = {value}")
            
            updated_count += 1

        # 7. 归档历史配置与变更履历
        try:
            current_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
            history_list = self.get_data("env_history") or []
            if changes and (not history_list or history_list[-1].get("content") != content):
                new_record = {
                    "time": current_time,
                    "updated": updated_count,
                    "deleted": deleted_count,
                    "content": content,
                    "changes": changes
                }
                history_list.append(new_record)
                if len(history_list) > 20:
                    history_list = history_list[-20:]
                self.save_data("env_history", history_list)
                logger.info(f"[EnvEditor] 配置变更记录成功归档。历史版本数: {len(history_list)}")
        except Exception as e:
            logger.error(f"[EnvEditor] 保存历史履历失败: {e}")

        logger.info(f"[EnvEditor] 环境增量热更新完成。新增/修改: {updated_count} 个，删除/重置: {deleted_count} 个。")
        return content

