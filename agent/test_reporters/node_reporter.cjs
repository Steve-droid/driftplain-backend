// Runs in node's runner process, separate from test files' child processes.
const path = require('node:path');
module.exports = async function* (source) {
  const fs = require('node:fs');
  const crypto = require('node:crypto');
  const lock = fs.readFileSync('/opt/driftplain-tests/package-lock.json');
  if (crypto.createHash('sha256').update(lock).digest('hex') !== process.env.DRIFTPLAIN_DEPENDENCY_SHA256 || !process.version.startsWith('v22.')) {
    throw new Error('Reviewed Node environment/lock mismatch');
  }
  const tests = [];
  const errors = [];
  for await (const event of source) {
    const d = event.data;
    if (event.type === 'test:stdout' || event.type === 'test:stderr') {
      // Prefix every line: test stdout cannot manufacture a reporter record.
      yield String(d.message).split('\n').map(x => 'test output: ' + x).join('\n') + '\n';
    }
    if (event.type === 'test:pass' || event.type === 'test:fail') {
      if (d.details?.type === 'suite') continue;
      const file = d.file ? path.relative('/workspace', d.file) : '';
      if (d.name === file || d.name === d.file) {
        if (event.type === 'test:fail') errors.push('test file failed before reporting a test');
        continue;
      }
      if (!file || file.startsWith('../') || !d.line) { errors.push('missing test location'); continue; }
      tests.push({id: file + ':' + d.line + ':' + d.column + ':' + d.name,
                  path: file,
                  status: d.skip || d.todo ? 'skipped' : event.type === 'test:pass' ? 'passed' : 'failed'});
      if (d.details?.error) yield 'test failure: ' + String(d.details.error.stack || d.details.error) + '\n';
    }
  }
  yield 'DRIFTPLAIN_TEST_REPORT_V1=' + JSON.stringify({version: 1, tests, errors}) + '\n';
};
