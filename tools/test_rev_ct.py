"""Model selection, trained decision semantics and the offline process boundary."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from app.inference.abnormality_adapter import select_model
from app.inference.ct_clip_adapter import ABNORMALITIES
from app.inference.rev_ct_adapter import classification_config,classification_result,run_classification


def predictions():
    return {'labels':list(ABNORMALITIES),'scores':[.2,.8]+[.1]*16,
            'raw_scores':[.6,.45]+[.2]*16,'decision_logits':[.7,-.3]+[-1]*16,
            'decisions':[True,False]+[False]*16}


class RevCTTests(unittest.TestCase):
    def test_legacy_option_preserves_model_identity_and_can_disable(self):
        self.assertEqual(select_model(),'rev_ct')
        self.assertEqual(select_model('none'),'none')
        self.assertEqual(select_model(ct_clip_classfine=True),'ct_clip_classfine')
        self.assertEqual(select_model(ct_clip_classfine=False),'none')
        self.assertEqual(select_model('rev_ct',True),'rev_ct')
        self.assertEqual(select_model('rev_ct',False),'rev_ct')
        self.assertEqual(select_model('none',True),'none')
        self.assertEqual(select_model('ct_clip_classfine',False),'ct_clip_classfine')
        with self.assertRaisesRegex(ValueError,'INVALID_CONFIG'):select_model('unknown')

    def test_calibrated_scores_do_not_replace_the_trained_decisions(self):
        result=classification_result(predictions(),{'manifest_digest':'a'*64})
        self.assertEqual(result['model'],'RevCT')
        self.assertIsNone(result['threshold'])
        self.assertTrue(result['abnormalities'][0]['flagged'])
        self.assertEqual(result['abnormalities'][0]['score'],.2)
        self.assertFalse(result['abnormalities'][1]['flagged'])
        self.assertEqual(result['abnormalities'][1]['score'],.8)

    def test_misordered_labels_invalid_values_and_inconsistent_decisions_fail(self):
        for key,value in [('labels',list(reversed(ABNORMALITIES))),('scores',[float('nan')]*18),
                          ('raw_scores',[1.1]*18),('decision_logits',[float('inf')]*18),
                          ('decisions',[1]*18),('decisions',[False]*18),('scores',[.5]*17)]:
            payload=predictions();payload[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'REV_CT_INVALID_OUTPUT'):
                classification_result(payload,{'manifest_digest':'a'*64})

    def test_config_requires_complete_local_files_and_executable_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=Path(tmp)
            settings=SimpleNamespace(rev_ct_model_cache=cache,rev_ct_manifest_digest='a'*64,
                                     rev_ct_python=None,timeout_seconds=10)
            with self.assertRaisesRegex(ValueError,'REV_CT_CHECKPOINT_UNAVAILABLE'):classification_config(settings)
            for name in ('checkpoint-manifest.json','config.json','processor_config.json','model.safetensors'):
                (cache/name).write_bytes(b'fixture')
            config=classification_config(settings)
            self.assertEqual(config['backend'],'rev_ct')
            settings.rev_ct_python=cache/'not-executable'
            settings.rev_ct_python.write_text('fixture')
            with self.assertRaisesRegex(ValueError,'REV_CT_MODEL_UNAVAILABLE'):classification_config(settings)

    def test_offline_subprocess_retains_gpu_lock_and_validates_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config={'python':sys.executable,'manifest_digest':'a'*64,'include_heatmaps':False}
            child=Mock(returncode=0);child.poll.return_value=0
            def start(command,**kwargs):
                self.assertEqual(command[0],sys.executable)
                self.assertEqual(kwargs['pass_fds'],(42,))
                self.assertEqual(kwargs['env']['HF_HUB_OFFLINE'],'1')
                self.assertEqual(kwargs['env']['TRANSFORMERS_OFFLINE'],'1')
                self.assertEqual(Path(kwargs['env']['TMPDIR']).resolve(),root/'tmp')
                (root/'predictions.json').write_text(json.dumps(predictions()))
                return child
            with patch('app.inference.rev_ct_adapter.subprocess.Popen',side_effect=start):
                result=run_classification(root/'native.nii.gz',config,root,lambda:True,42)
            self.assertEqual(result['model'],'RevCT')

    def test_cancellation_and_timeout_terminate_process(self):
        for cancelled in (True,False):
            with self.subTest(cancelled=cancelled),tempfile.TemporaryDirectory() as tmp:
                child=Mock();child.poll.return_value=None
                guard=Mock(side_effect=[True,False]) if cancelled else lambda:True
                with patch('app.inference.rev_ct_adapter.subprocess.Popen',return_value=child),patch('app.inference.rev_ct_adapter.terminate') as terminate:
                    with self.assertRaisesRegex(ValueError,'LEASE_LOST' if cancelled else 'REV_CT_TIMEOUT'):
                        run_classification(Path(tmp)/'input',{'python':sys.executable,'timeout_seconds':10 if cancelled else -1},tmp,guard)
                    terminate.assert_called_once_with(child)


if __name__=='__main__':unittest.main()
