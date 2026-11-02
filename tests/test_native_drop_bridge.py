"""Regressão: arrastos internos nunca atravessam a serialização DOM do pywebview."""
import json
import os
import subprocess
from types import SimpleNamespace

from import_queue import ImportAPI


def test_native_drop_bridge_filters_in_browser_before_serialization():
    operations = []

    class Events:
        def __init__(self, name):
            self.name = name

        def __iadd__(self, handler):
            operations.append({'event': self.name})
            return self

    api = ImportAPI()
    api._window = SimpleNamespace(
        run_js=lambda script: operations.append({'script': script}),
        dom=SimpleNamespace(document=SimpleNamespace(events=SimpleNamespace(
            drop=Events('drop'), dragover=Events('dragover')))))
    api._bind_native_drop()
    script = r'''
const assert = require('node:assert/strict');
global.document = new EventTarget();
let serialized = 0;
for (const op of JSON.parse(process.argv[1])) {
  if (op.script) eval(op.script);
  else document.addEventListener(op.event, () => serialized++);
}
function dispatch(type, types) {
  const event = new Event(type, {cancelable:true});
  event.dataTransfer = {types};
  document.dispatchEvent(event);
  return event;
}
for (let i=0; i<100; i++) dispatch('dragover', ['text/plain']);
assert.equal(serialized, 0, 'internal dragover must never serialize DOM');
assert.equal(dispatch('dragover', ['Files']).defaultPrevented, true);
assert.equal(serialized, 0, 'file dragover stays entirely in JavaScript');
dispatch('drop', ['text/plain']);
dispatch('drop', []);
assert.equal(serialized, 0, 'internal drop must be filtered before native bridge');
dispatch('drop', ['Files']);
assert.equal(serialized, 1, 'file drop must reach the full-path native handler');
'''
    result = subprocess.run([os.environ.get('NODE_BINARY', 'node'), '-e', script, json.dumps(operations)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
