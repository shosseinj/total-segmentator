"""Swagger exposes auth controls while preserving API authentication checks."""
from types import SimpleNamespace
import unittest

from fastapi import Depends,FastAPI
from fastapi.testclient import TestClient

from app.auth import authenticate
from app.main import create_app


class ApiDocsTests(unittest.TestCase):
    def test_upload_response_links_to_a_standalone_sample(self):
        with TestClient(create_app(None)) as client:
            docs=client.get('/docs')
            self.assertIn('href="/docs/samples/viewer.html"',docs.text)
            self.assertIn('href="/docs/samples/viewer.html?download=true"',docs.text)
            schema=client.get('/openapi.json').json()
            operation=schema['paths']['/v1/upload/']['post']
            self.assertIn('[نمایش viewer.html](/docs/samples/viewer.html)',operation['description'])
            response=operation['responses']['200']
            example=response['content']['text/html']['examples']['viewer']
            # External examples are eagerly fetched by Swagger; the complete
            # embedded viewer must only load after following its link.
            self.assertNotIn('externalValue',example)
            self.assertTrue(example['value'].startswith('<!doctype html>'))
            self.assertLess(len(example['value']),1000)
            sample_url=operation['externalDocs']['url']
            self.assertNotIn(sample_url,schema['paths'])
            sample=client.get(sample_url)
            self.assertEqual(sample.status_code,200)
            self.assertTrue(sample.headers['content-type'].startswith('text/html'))
            self.assertNotIn('content-disposition',sample.headers)
            self.assertIn('SYNTHETIC DEMO',sample.text)
            self.assertIn('const data=',sample.text)
            self.assertIn('themePicker',sample.text)
            self.assertNotIn('__DATA__',sample.text)
            download=client.get(sample_url+'?download=true')
            self.assertEqual(download.headers['content-disposition'],response['headers']['Content-Disposition']['example'])
            self.assertEqual(download.content,sample.content)

    def test_docs_redirect_and_security_schema(self):
        with TestClient(create_app(None)) as client:
            response=client.get('/',follow_redirects=False)
            self.assertEqual(response.status_code,307)
            self.assertEqual(response.headers['location'],'/docs')
            response=client.get('/docs')
            self.assertEqual(response.status_code,200)
            self.assertIn('SwaggerUIBundle',response.text)
            schema=client.get('/openapi.json').json()
        self.assertEqual(schema['components']['securitySchemes']['ServiceToken']['scheme'],'bearer')
        operation=schema['paths']['/v1/capabilities']['get']
        self.assertEqual(operation['security'],[{'ServiceToken':[]}])
        headers={p['name'] for p in operation['parameters'] if p['in']=='header'}
        self.assertTrue({'X-Tenant-Id','X-Actor-Id','X-Operator'}<=headers)
        self.assertNotIn('security',schema['paths']['/health/live']['get'])

    def test_authentication_and_scope_are_preserved(self):
        app=FastAPI()
        app.state.settings=SimpleNamespace(service_token='example-service-token',tenant_id='example-tenant')

        @app.get('/probe')
        def probe(actor=Depends(authenticate)):
            return {'tenant':actor.tenant_id,'actor':actor.actor_id,'operator':actor.operator}

        headers={'Authorization':'Bearer example-service-token','X-Tenant-Id':'example-tenant','X-Actor-Id':'operator','X-Operator':'true'}
        with TestClient(app) as client:
            response=client.get('/probe',headers=headers)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json(),{'tenant':'example-tenant','actor':'operator','operator':True})
            for token in ('','Bearer wrong-token','Basic example-service-token'):
                self.assertEqual(client.get('/probe',headers={**headers,'Authorization':token}).status_code,401)
            for field,value in [('X-Tenant-Id','other'),('X-Actor-Id','invalid actor')]:
                self.assertEqual(client.get('/probe',headers={**headers,field:value}).status_code,403)


if __name__=='__main__':
    unittest.main()
