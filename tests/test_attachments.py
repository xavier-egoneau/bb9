from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bb9.core.attachments import resolve_image_attachments
from bb9.core.channels import intention_from_text
from bb9.core.kernel import Kernel
from bb9.core.models import Intention, RunContext, Session, Workspace
from bb9.core.providers import OpenAICompatibleProvider


class AttachmentTests(unittest.TestCase):
    def test_channel_parses_image_refs_without_removing_text(self) -> None:
        intention = intention_from_text("regarde ça\n[image: .bb9/uploads/web/a.png]")

        self.assertEqual((" .bb9/uploads/web/a.png".strip(),), intention.metadata["image_refs"])
        self.assertIn("[image:", intention.text)

    def test_resolve_image_attachments_allows_workspace_uploads_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            upload = workspace / ".bb9" / "uploads" / "web" / "a.png"
            outside = workspace / "a.png"
            upload.parent.mkdir(parents=True)
            upload.write_bytes(b"png")
            outside.write_bytes(b"png")

            images = resolve_image_attachments(f"[image: {upload}]\n[image: {outside}]", workspace)

            self.assertEqual(1, len(images))
            self.assertEqual(upload.resolve(), images[0].path)

    def test_kernel_passes_images_to_multimodal_provider(self) -> None:
        class CapturingProvider:
            prompt = ""
            images = ()

            def complete(self, prompt: str, *, images: tuple = ()) -> str:
                self.prompt = prompt
                self.images = images
                return "image ok"

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            image = workspace / ".bb9" / "uploads" / "web" / "screen.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            provider = CapturingProvider()

            decision = Kernel(provider=provider).decide(
                Intention(f"Décris [image: {image}]"),
                RunContext(session=Session(), workspace=Workspace(root=workspace)),
            )

            self.assertEqual("image ok", decision.summary)
            self.assertEqual(1, len(provider.images))
            self.assertIn("# Images jointes", provider.prompt)
            self.assertNotIn("[image:", provider.prompt)

    def test_openai_compatible_provider_sends_image_content(self) -> None:
        payloads: list[dict] = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self) -> bytes:
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

        def fake_urlopen(request, timeout=0):
            payloads.append(json.loads(request.data.decode("utf-8")))
            return Response()

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            image = workspace / ".bb9" / "uploads" / "web" / "screen.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            images = resolve_image_attachments(f"[image: {image}]", workspace)

            with patch.dict("os.environ", {"OPENAI_API_KEY": "secret"}), patch("bb9.core.providers.urlopen", fake_urlopen):
                result = OpenAICompatibleProvider(model="gpt-test").complete("bonjour", images=images)

        self.assertEqual("ok", result)
        content = payloads[0]["messages"][0]["content"]
        self.assertEqual("text", content[0]["type"])
        self.assertEqual("image_url", content[1]["type"])
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main()
