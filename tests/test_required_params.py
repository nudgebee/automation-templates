import importlib.util
import os
import unittest

_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "validate.py")
_spec = importlib.util.spec_from_file_location("validate", _path)
validate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate)


def errors_for(tasks=None, triggers=None, hooks=None):
    doc = {
        "name": "Test Template",
        "definition": {
            "version": "v1",
            "triggers": triggers if triggers is not None else [{"type": "manual"}],
            "tasks": tasks or [{"id": "say_hi", "type": "core.print", "params": {"message": "hi"}}],
        },
    }
    if hooks is not None:
        doc["definition"]["hooks"] = hooks
    return validate.validate_workflow_semantics(doc, "templates/test.yaml")


class RequiredParams(unittest.TestCase):
    def test_missing_required_param_is_rejected(self):
        tasks = [{"id": "resize", "type": "k8s.vertical_rightsize",
                  "params": {"namespace": "{{ Inputs.namespace }}", "name": "web", "kind": "Deployment"}}]
        self.assertEqual(
            ["task 'resize': missing required parameter 'direction' for k8s.vertical_rightsize"],
            errors_for(tasks=tasks),
        )

    def test_empty_string_counts_as_missing(self):
        tasks = [{"id": "argo", "type": "cicd.argocd.cli", "params": {"command": "app get web", "integration_id": ""}}]
        self.assertEqual(
            ["task 'argo': missing required parameter 'integration_id' for cicd.argocd.cli"],
            errors_for(tasks=tasks),
        )

    def test_required_param_with_engine_default_is_not_flagged(self):
        tasks = [{"id": "resize_pv", "type": "k8s.pv_rightsize",
                  "params": {"namespace": "db", "name": "data", "gitops_config": {"repo": "x"}}}]
        self.assertEqual([], errors_for(tasks=tasks))

    def test_templated_value_is_accepted(self):
        tasks = [{"id": "argo", "type": "cicd.argocd.cli",
                  "params": {"command": "app get web", "integration_id": "{{ Inputs.argocd_integration_id }}"}}]
        self.assertEqual([], errors_for(tasks=tasks))

    def test_task_hook_is_checked(self):
        tasks = [{"id": "say_hi", "type": "core.print", "params": {"message": "hi"},
                  "hooks": {"success": [{"type": "notifications.im", "params": {"provider": "slack", "message": "done"}}]}}]
        self.assertEqual(
            ["hook: missing required parameter 'channel' for notifications.im"],
            errors_for(tasks=tasks),
        )

    def test_workflow_hook_is_checked(self):
        hooks = {"failure": [{"type": "notifications.email", "params": {"subject": "failed", "body": "x"}}]}
        self.assertEqual(
            ["hook: missing required parameter 'recipients' for notifications.email"],
            errors_for(hooks=hooks),
        )


class ScheduleCron(unittest.TestCase):
    def test_templated_cron_is_rejected(self):
        self.assertEqual(
            ["schedule trigger cron '{{ Inputs.cron_schedule }}' must be a literal cron expression (5 fields or @descriptor)"],
            errors_for(triggers=[{"type": "schedule", "params": {"cron": "{{ Inputs.cron_schedule }}"}}]),
        )

    def test_literal_cron_is_accepted(self):
        for cron in ["*/5 * * * *", "CRON_TZ=Asia/Kolkata 0 9 * * *", "@daily"]:
            self.assertEqual([], errors_for(triggers=[{"type": "schedule", "params": {"cron": cron}}]), cron)

    def test_non_dict_params_do_not_crash(self):
        self.assertEqual(
            ["task 'pause': missing required parameter 'duration' for core.wait"],
            errors_for(tasks=[{"id": "pause", "type": "core.wait", "params": ["oops"]}]),
        )


if __name__ == "__main__":
    unittest.main()
