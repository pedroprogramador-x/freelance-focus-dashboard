export function ProgressRing({ value, size = 108 }: { value: number; size?: number }) {
  const radius = 42
  const circumference = 2 * Math.PI * radius
  return <div className="progress-ring" style={{ width: size, height: size }}>
    <svg viewBox="0 0 100 100" aria-hidden="true"><circle className="ring-track" cx="50" cy="50" r={radius} /><circle className="ring-value" cx="50" cy="50" r={radius} strokeDasharray={circumference} strokeDashoffset={circumference - value / 100 * circumference} /></svg>
    <strong>{value}%</strong>
  </div>
}
