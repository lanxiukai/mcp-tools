// Read one bounded ELK graph; return only its layout. No network or lifecycle hooks.
const ELK = require('../node_modules/elkjs');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  input += chunk;
  if (Buffer.byteLength(input) > 1048576) {
    process.stderr.write('ELK input exceeds 1 MiB');
    process.exit(1);
  }
});
process.stdin.on('end', async () => {
  try {
    const graph = JSON.parse(input);
    const elk = new ELK();
    const result = await elk.layout(graph);
    process.stdout.write(JSON.stringify(result, (key, value) => key.startsWith('$') ? undefined : value));
  } catch (error) {
    process.stderr.write(String(error.message).slice(0, 1000));
    process.exitCode = 1;
  }
});
