export const Badge = ({ s }: { s: string }) => <span className={`badge s-${s}`}>{s.replace("_", " ")}</span>;
