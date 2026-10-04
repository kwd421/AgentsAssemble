// The edge supplies CF-Connecting-IP. IPv6 interface-address rotation within
// an ordinary /64 must not create new coarse or durable abuse allowances.
export function networkSource(request) {
  const address = (request.headers.get("cf-connecting-ip") || "unknown").trim();
  if (!address.includes(":")) return address;
  try {
    // WHATWG URL parsing validates IPv6 and canonicalizes embedded IPv4 too.
    const canonical = new URL(`http://[${address}]/`).hostname.slice(1, -1);
    const [left, right] = canonical.split("::");
    const head = left ? left.split(":") : [];
    const tail = right ? right.split(":") : [];
    const words = right === undefined ? head : [...head, ...Array(8 - head.length - tail.length).fill("0"), ...tail];
    return `${words.slice(0, 4).map(word => word.padStart(4, "0")).join(":")}::/64`;
  } catch {
    return "unknown";
  }
}
