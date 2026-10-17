// Shared deterministic calculation. No wallet, network or model call.
export function textStatistics(buffer) {
  if (!(buffer instanceof Uint8Array) || !buffer.length || buffer.length > 1024 * 1024) throw Error('nonempty input <= 1 MiB required');
  const source = new TextDecoder('utf-8', { fatal: true }).decode(buffer);
  if (!source.trim()) throw Error('empty text is not a valid task');
  const normalized = source.replace(/\r\n/g, '\n');
  return { byteCount: buffer.length, characterCount: [...source].length,
    wordCount: (normalized.match(/\S+/gu) || []).length,
    lineCount: normalized.split('\n').length - (normalized.endsWith('\n') ? 1 : 0) };
}
