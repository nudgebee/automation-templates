import importlib.util
import os
import unittest

_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "validate.py")
_spec = importlib.util.spec_from_file_location("validate", _path)
validate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate)


def errors_for(tasks=None, triggers=None, inputs=None):
    doc = {
        "name": "Test Template",
        "definition": {
            "version": "v1",
            "inputs": inputs or [],
            "triggers": triggers if triggers is not None else [{"type": "manual"}],
            "tasks": tasks or [{"id": "say_hi", "type": "core.print", "params": {"message": "hi"}}],
        },
    }
    return validate.validate_workflow_semantics(doc, "templates/test.yaml")


class InputDefaults(unittest.TestCase):
    def test_example_dot_com_default_is_rejected(self):
        self.assertEqual(
            ["input 'domains': default ['example.com'] is a placeholder; leave it empty and mark the input required"],
            errors_for(inputs=[{"id": "domains", "type": "array", "default": ["example.com"]}]),
        )

    def test_fixed_region_default_is_rejected(self):
        self.assertEqual(
            ["input 'aws_region': default region 'us-east-1' silently limits every run to it; make the input required"],
            errors_for(inputs=[{"id": "aws_region", "type": "string", "default": "us-east-1"}]),
        )

    def test_region_without_default_must_be_required(self):
        expected = ["input 'aws_region': region input must be required: true so the form asks for it"]
        self.assertEqual(expected, errors_for(inputs=[{"id": "aws_region", "type": "string", "required": False}]))
        self.assertEqual(expected, errors_for(inputs=[{"id": "aws_region", "type": "string"}]))

    def test_real_defaults_pass(self):
        inputs = [
            {"id": "aws_region", "type": "string", "required": True},
            {"id": "base_url", "type": "string", "default": "https://api.pagerduty.com"},
            {"id": "tcp_port", "type": "string", "default": "443"},
        ]
        self.assertEqual([], errors_for(inputs=inputs))


if __name__ == "__main__":
    unittest.main()
