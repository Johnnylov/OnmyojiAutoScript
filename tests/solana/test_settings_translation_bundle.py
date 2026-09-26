import json
import unittest

from dev_tools.sync_oasx_translations import (
    BUNDLE,
    CATALOG,
    SOURCE,
    render_bundle,
    settings_catalog,
)


class SettingsTranslationBundleTests(unittest.TestCase):
    def test_bundled_frontend_dictionary_matches_backend_dictionary(self):
        translations = json.loads(SOURCE.read_text(encoding="utf-8"))
        self.assertEqual(BUNDLE.read_text(encoding="utf-8"), render_bundle(translations))

    def test_translation_coverage_catalog_matches_current_registered_schemas(self):
        self.assertEqual(json.loads(CATALOG.read_text(encoding="utf-8")), settings_catalog())


if __name__ == "__main__":
    unittest.main()
