// A bounded, local-only MathJax worker. JSON input and output; no SVG on stderr.
const path = require('node:path');
const root = process.env.MATHJAX_NODE_PATH || path.join(__dirname, '..', '..', 'format-conversion');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  input += chunk;
  if (Buffer.byteLength(input) > 524288) {
    process.stderr.write('Math input exceeds 512 KiB');
    process.exit(1);
  }
});
process.stdin.on('end', async () => {
  let MJ;
  try {
    const formulas = JSON.parse(input);
    if (!Array.isArray(formulas) || formulas.length > 128 ||
        formulas.some(tex => typeof tex !== 'string' || tex.length > 4096)) {
      throw new Error('Expected at most 128 formulas of at most 4096 characters');
    }
    MJ = require(require.resolve('mathjax', {paths: [root]}));
    await MJ.init({
      loader: {load: ['input/tex-base', '[tex]/ams', 'output/svg']},
      tex: {
        packages: ['base', 'ams'], maxBuffer: 16384,
        formatError: (_, error) => { throw new Error(error.message); }
      },
      svg: {fontCache: 'local'},
      output: {linebreaks: {inline: false}}
    });
    const adaptor = MJ.startup.adaptor;
    const results = [];
    for (const latex of formulas) {
      const node = await MJ.tex2svgPromise(latex, {display: false});
      const svg = adaptor.tags(node, 'svg')[0];
      const markup = adaptor.serializeXML(svg);
      if (/data-mjx-error|data-mml-node="merror"/.test(markup)) {
        throw new Error(`Invalid formula at index ${results.length}`);
      }
      results.push(markup);
    }
    process.stdout.write(JSON.stringify(results));
  } catch (error) {
    process.stderr.write(String(error.message).slice(0, 1000));
    process.exitCode = 1;
  } finally {
    if (MJ && typeof MJ.done === 'function') MJ.done();
  }
});
