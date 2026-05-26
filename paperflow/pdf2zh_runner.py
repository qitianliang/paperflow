import os
import sys
import subprocess
import json
import time
from typing import Dict, Any, Tuple, Optional
from paperflow.config import get_config
from paperflow.logging_utils import get_logger

logger = get_logger(__name__)

class PDF2ZHRunner:
    def __init__(self, model_tier: Optional[str] = None, model: Optional[str] = None):
        self.config = get_config()
        self.trans_config = self.config.translation.pdf2zh

        self.executable = self.trans_config.executable_default

        # Auto-resolve: check env var (if real file), then conda Scripts, then PATH
        env_exe = os.environ.get(self.trans_config.executable_env)
        conda_exe = os.path.join(os.path.dirname(sys.executable), "Scripts", "pdf2zh.exe")
        if env_exe and os.path.exists(env_exe):
            self.executable = env_exe
        elif os.path.exists(conda_exe):
            self.executable = conda_exe
        elif env_exe:
            self.executable = env_exe  # might be in PATH

        # Resolve model: CLI override > config model_tier > default
        self.model_tier = model_tier or self.trans_config.model_tier
        self.model = model or self._resolve_model(self.model_tier)

    def _resolve_model(self, tier: str) -> str:
        """Resolve model name from the unified llm.providers config (single source of truth).
        Env var `LLM_MODEL_{TIER}` overrides yaml."""
        provider_name = self.trans_config.llm_provider
        provider = self.config.llm.providers.get(provider_name)
        if provider:
            return provider.resolved_model(tier) or provider.resolved_model("balanced") or ""
        return "deepseek-chat"

    def _generate_pdf2zh_config(self) -> str:
        """Generate a pdf2zh config JSON file for use with --config flag."""
        provider_name = self.trans_config.llm_provider
        provider = self.config.llm.providers.get(provider_name)

        base_url = provider.resolved_base_url if provider else None
        api_key_env = provider.api_key_env if provider else "DEEPSEEK_API_KEY"

        config_data = {
            "service": self.trans_config.service,
            "model": self.model,
            "base_url": base_url,
            "api_key_env": api_key_env,
        }
        config_path = self.trans_config.config_file
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        return config_path

    def run_translation(self, zotero_key: str, input_pdf_path: str) -> Tuple[bool, str, str, str]:
        """
        Runs pdf2zh on the given PDF.
        Returns: (success_bool, mono_pdf_path, dual_pdf_path, error_message)
        """
        output_dir = os.path.join(
            self.config.translation.paths.output_dir,
            self.config.project.topic_slug,
            zotero_key,
        )
        os.makedirs(output_dir, exist_ok=True)

        service = self.trans_config.service
        threads = str(self.trans_config.threads)
        source_lang = self.trans_config.source_lang
        target_lang = self.trans_config.target_lang

        provider_name = self.trans_config.llm_provider
        provider = self.config.llm.providers.get(provider_name)
        base_url = provider.resolved_base_url if provider else None

        cmd = [
            self.executable,
            input_pdf_path,
            "-s", f"{service}:{self.model}",
            "-t", threads,
            "-li", source_lang,
            "-lo", target_lang,
            "-o", output_dir
        ]

        # Generate pdf2zh config file and pass it
        config_path = self._generate_pdf2zh_config()
        cmd.extend(["--config", config_path])

        logger.info(f"Running pdf2zh for {zotero_key} [model: {service}:{self.model}]...")
        logger.debug(f"Command: {' '.join(cmd)}")

        # Prepare env for base_url and API key
        env = os.environ.copy()
        if base_url:
            env["OPENAI_BASE_URL"] = base_url
            env["DEEPSEEK_BASE_URL"] = base_url

        # Pass API key via env
        api_key_env_var = provider.api_key_env if provider else "DEEPSEEK_API_KEY"
        api_key = os.environ.get(api_key_env_var) or (provider.api_key if provider else "")
        if api_key:
            env[api_key_env_var] = api_key
            if service == "openai":
                env["OPENAI_API_KEY"] = api_key

        start_time = time.time()
        timeout = self.trans_config.timeout_minutes * 60

        try:
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            end_time = time.time()
            logger.info(f"pdf2zh finished in {end_time - start_time:.2f}s. Return code: {result.returncode}")

            if result.returncode != 0:
                logger.error(f"pdf2zh failed: {result.stderr}")
                return False, "", "", result.stderr

            # Scan output directory for the generated PDFs
            mono_pdf = ""
            dual_pdf = ""

            for file in os.listdir(output_dir):
                if file.endswith("-mono.pdf"):
                    mono_pdf = os.path.join(output_dir, file)
                elif file.endswith("-dual.pdf"):
                    dual_pdf = os.path.join(output_dir, file)

            if not mono_pdf and not dual_pdf:
                pdfs = [f for f in os.listdir(output_dir) if f.endswith('.pdf')]
                if len(pdfs) == 1:
                    mono_pdf = os.path.join(output_dir, pdfs[0])
                elif len(pdfs) >= 2:
                    pdfs.sort(key=len)
                    mono_pdf = os.path.join(output_dir, pdfs[0])
                    dual_pdf = os.path.join(output_dir, pdfs[1])

            if not mono_pdf and not dual_pdf:
                 return False, "", "", "Translation command succeeded but no output PDFs were found."

            return True, mono_pdf, dual_pdf, ""

        except subprocess.TimeoutExpired:
            logger.error(f"pdf2zh timed out after {timeout} seconds.")
            return False, "", "", "Timeout"
        except Exception as e:
            logger.error(f"pdf2zh execution error: {e}")
            return False, "", "", str(e)
