# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml==6.0.2"]
# ///
"""Render the chart and verify its authentication and transport boundaries.

Run with: uv run scripts/helm/test_chart.py
No cluster, gateway, identity provider, or real credentials are needed.
"""

import pathlib
import subprocess
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy/helm/openshell-dashboard"


def render(mode="oauth2-proxy", overrides=None):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as values:
        yaml.safe_dump(overrides or {}, values)
        values.flush()
        return subprocess.run(
            ["helm", "template", "test", str(CHART), "--namespace", "dashboard",
             "-f", str(CHART / "ci" / f"{mode}.yaml"), "-f", values.name],
            text=True, capture_output=True, check=False,
        )


def resource(documents, kind):
    return next(doc for doc in documents if doc["kind"] == kind)


def env(container):
    return {entry["name"]: entry.get("value", entry.get("valueFrom"))
            for entry in container["env"]}


class ChartTests(unittest.TestCase):
    def documents(self, mode="oauth2-proxy", overrides=None):
        result = render(mode, overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        return list(yaml.safe_load_all(result.stdout))

    def test_all_modes_lint_and_package(self):
        for mode in ("oauth2-proxy", "external", "development"):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    ["helm", "lint", "--strict", str(CHART), "-f",
                     str(CHART / "ci" / f"{mode}.yaml")],
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with tempfile.TemporaryDirectory() as destination:
            subprocess.run(["helm", "package", str(CHART), "-d", destination], check=True)

    def test_proxy_is_the_only_network_entry_point(self):
        docs = self.documents()
        pod = resource(docs, "Deployment")["spec"]["template"]["spec"]
        dashboard, proxy = pod["containers"]
        self.assertEqual(env(dashboard)["LISTEN_ADDRESS"], "127.0.0.1")
        self.assertEqual(env(dashboard)["AUTH_DISABLED"], "false")
        self.assertFalse(pod["automountServiceAccountToken"])
        service = resource(docs, "Service")["spec"]
        self.assertEqual(service["type"], "ClusterIP")
        self.assertEqual(service["ports"][0]["targetPort"], "proxy")
        self.assertIn("--upstream=http://127.0.0.1:8080/", proxy["args"])
        self.assertIn("--pass-access-token=true", proxy["args"])
        self.assertIn("--skip-auth-strip-headers=true", proxy["args"])
        self.assertIn("--cookie-secure=true", proxy["args"])
        self.assertIn("--proxy-websockets=true", proxy["args"])
        self.assertEqual(env(dashboard)["AUTH_TOKEN_HEADER"], "x-forwarded-access-token")
        self.assertEqual(env(dashboard)["AUTH_USER_HEADER"], "x-forwarded-user")
        self.assertIn("/healthz", dashboard["livenessProbe"]["exec"]["command"][-1])
        self.assertIn("/readyz", dashboard["readinessProbe"]["exec"]["command"][-1])
        self.assertEqual(resource(docs, "Ingress")["spec"]["tls"][0]["secretName"], "dashboard-tls")

    def test_secrets_are_referenced_not_generated(self):
        docs = self.documents()
        self.assertEqual({doc["kind"] for doc in docs}, {"Deployment", "Service", "Ingress"})
        pod = resource(docs, "Deployment")["spec"]["template"]["spec"]
        dashboard, proxy = pod["containers"]
        self.assertEqual(env(proxy)["OAUTH2_PROXY_CLIENT_SECRET"], {
            "secretKeyRef": {"name": "dashboard-oauth2", "key": "client-secret"},
        })
        self.assertEqual(env(proxy)["OAUTH2_PROXY_COOKIE_SECRET"], {
            "secretKeyRef": {"name": "dashboard-oauth2", "key": "cookie-secret"},
        })
        self.assertEqual(env(dashboard)["GATEWAY_CLIENT_KEY"], "/etc/gateway-tls/tls.key")
        self.assertEqual(env(dashboard)["GATEWAY_CA_CERT"], "/etc/gateway-tls/ca.crt")
        self.assertIn("--provider-ca-file=/etc/issuer-ca/ca.crt", proxy["args"])
        self.assertTrue(all(mount["readOnly"] for c in pod["containers"] for mount in c["volumeMounts"]))

    def test_external_proxy_is_restricted_to_selected_pods_and_namespace(self):
        docs = self.documents("external")
        pod = resource(docs, "Deployment")["spec"]["template"]["spec"]
        self.assertEqual(len(pod["containers"]), 1)
        self.assertEqual(env(pod["containers"][0])["AUTH_DISABLED"], "false")
        policy = resource(docs, "NetworkPolicy")["spec"]
        peer = policy["ingress"][0]["from"][0]
        self.assertEqual(peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"], "authentication")
        self.assertEqual(peer["podSelector"]["matchLabels"]["app.kubernetes.io/name"], "trusted-auth-proxy")
        self.assertEqual(policy["podSelector"], resource(docs, "Deployment")["spec"]["selector"])
        self.assertEqual(policy["ingress"][0]["ports"], [{"protocol": "TCP", "port": 8080}])
        self.assertNotIn("Ingress", {doc["kind"] for doc in docs})

    def test_development_is_explicit_and_has_no_auth_infrastructure(self):
        docs = self.documents("development")
        self.assertEqual({doc["kind"] for doc in docs}, {"Deployment", "Service"})
        pod = resource(docs, "Deployment")["spec"]["template"]["spec"]
        self.assertEqual(len(pod["containers"]), 1)
        self.assertEqual(env(pod["containers"][0])["AUTH_DISABLED"], "true")

    def test_digest_and_custom_names(self):
        digest = "sha256:" + "a" * 64
        docs = self.documents("development", {"image": {"tag": "", "digest": digest},
                                              "fullnameOverride": "my-dashboard"})
        deployment = resource(docs, "Deployment")
        self.assertEqual(deployment["metadata"]["name"], "my-dashboard")
        self.assertTrue(deployment["spec"]["template"]["spec"]["containers"][0]["image"].endswith("@" + digest))
        self.assertEqual(resource(docs, "Service")["spec"]["selector"], deployment["spec"]["selector"]["matchLabels"])

    def test_invalid_configuration_fails_before_installation(self):
        cases = [
            ("development", {"gateway": {"url": ""}}, "gateway.url is required"),
            ("development", {"image": {"tag": ""}}, "image.tag"),
            ("development", {"auth": {"mode": "typo"}}, "auth.mode"),
            ("oauth2-proxy", {"oauth2Proxy": {"issuerUrl": "http://idp.example.com"}}, "issuerUrl"),
            ("oauth2-proxy", {"oauth2Proxy": {"redirectUrl": "https://dashboard.example.com"}}, "redirectUrl"),
            ("oauth2-proxy", {"oauth2Proxy": {"clientId": ""}}, "clientId"),
            ("oauth2-proxy", {"oauth2Proxy": {"existingSecret": ""}}, "existingSecret"),
            ("oauth2-proxy", {"oauth2Proxy": {"emailDomains": []}}, "emailDomains"),
            ("oauth2-proxy", {"gateway": {"tls": {"clientKeyKey": ""}}}, "must be set together"),
            ("oauth2-proxy", {"gateway": {"tls": {"existingSecret": ""}}}, "required for mTLS"),
            ("external", {"auth": {"external": {"allowedPeers": []}}}, "allowedPeers"),
            ("external", {"auth": {"external": {"allowedPeers": [{}]}}}, "podSelector"),
            ("external", {"auth": {"external": {"allowedPeers": [{"podSelector": {}}]}}}, "must not be empty"),
            ("external", {"auth": {"external": {"allowedPeers": [{"podSelector": {"matchLabels": {}}}]}}}, "must not be empty"),
            ("external", {"ingress": {"enabled": True}}, "external proxy"),
            ("oauth2-proxy", {"ingress": {"tlsSecretName": ""}}, "tlsSecretName"),
            ("oauth2-proxy", {"ingress": {"host": "other.example.com"}}, "must use ingress.host"),
        ]
        for mode, overrides, message in cases:
            with self.subTest(mode=mode, overrides=overrides):
                result = render(mode, overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
