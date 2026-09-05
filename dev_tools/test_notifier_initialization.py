"""Notification failure regressions: no external provider or network calls."""

from smtplib import SMTPResponseException
from unittest.mock import Mock
import unittest

import yaml
from requests import Response

from test_mystery_shop_scroll import ROOT, class_from_file


class NotifierInitializationTests(unittest.TestCase):
    def setUp(self):
        class Provider:
            params = {'required': ['token']}

            def __init__(self):
                self.notify = Mock(return_value=True)

        class Custom(Provider):
            pass

        class OnePushException(Exception):
            pass

        self.provider = Provider()
        self.namespace = {
            'yaml': yaml, 'logger': Mock(), 'Provider': Provider, 'Custom': Custom,
            'OnePushException': OnePushException, 'Response': Response,
            'SMTPResponseException': SMTPResponseException,
            'get_notifier': Mock(return_value=self.provider),
        }
        self.notifier_class = class_from_file(
            ROOT / 'module/notify/notify.py', 'Notifier', self.namespace
        )

    def test_malformed_yaml_does_not_raise_a_second_error(self):
        notifier = self.notifier_class('provider: [broken', enable=True)
        self.assertEqual(notifier.config, {})
        self.assertFalse(notifier.push(title='Original game error', content='Details'))
        self.namespace['get_notifier'].assert_not_called()
        self.provider.notify.assert_not_called()

    def test_invalid_or_empty_documents_are_safe(self):
        for config in ('', 'null', 'plain text', '123', '---\n---'):
            with self.subTest(config=config):
                notifier = self.notifier_class(config, enable=True)
                self.assertFalse(notifier.push(title='Original game error'))
        self.provider.notify.assert_not_called()

    def test_missing_provider_is_safe(self):
        notifier = self.notifier_class('token: offline-test', enable=True)
        self.assertFalse(notifier.push(title='Original game error'))
        self.provider.notify.assert_not_called()

    def test_provider_initialization_failure_is_safe(self):
        for error in (self.namespace['OnePushException']('unsupported'), RuntimeError('init failed')):
            with self.subTest(error=type(error).__name__):
                self.namespace['get_notifier'].side_effect = error
                notifier = self.notifier_class('provider: offline', enable=True)
                self.assertFalse(notifier.push(title='Original game error'))
        self.provider.notify.assert_not_called()

    def test_disabled_notifier_does_not_parse_or_send(self):
        notifier = self.notifier_class('provider: [broken', enable=False)
        self.assertFalse(notifier.push(title='Original game error'))
        self.namespace['get_notifier'].assert_not_called()
        self.provider.notify.assert_not_called()

    def test_valid_configuration_keeps_original_success_path(self):
        notifier = self.notifier_class('provider: offline\ntoken: offline-test', enable=True)
        notifier.config_name = 'oas2'
        self.assertTrue(notifier.push(title='Original game error', content='Details'))
        self.provider.notify.assert_called_once_with(
            token='offline-test', title='oas2 Original game error', content='Details'
        )


if __name__ == '__main__':
    unittest.main()
