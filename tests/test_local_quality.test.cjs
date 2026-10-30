const test = require('node:test');
const assert = require('node:assert/strict');
const {dictionary} = require('../ui/local-quality.js');
test('confirmed vocabulary keeps literal terms without learning replacements', () => {
  assert.deepEqual(dictionary(' TitaNet\nLucas\nTitaNet\n\n'), ['TitaNet', 'Lucas']);
  assert.deepEqual(dictionary('foo -> bar'), ['foo -> bar']);
});
