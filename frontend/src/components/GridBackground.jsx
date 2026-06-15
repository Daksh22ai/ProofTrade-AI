export default function GridBackground() {
  return (
    <div className="fixed inset-0 z-0 pointer-events-none overflow-hidden">
      {/* Animated grid */}
      <div className="absolute inset-0 grid-bg opacity-100" />

      {/* Radial gradient glow at top-center */}
      <div
        className="absolute top-0 left-1/2 -translate-x-1/2 w-[800px] h-[400px] rounded-full opacity-[0.07]"
        style={{ background: 'radial-gradient(ellipse, #00D4AA 0%, transparent 70%)' }}
      />

      {/* Corner accents */}
      <div
        className="absolute top-0 left-0 w-64 h-64 opacity-[0.04]"
        style={{ background: 'radial-gradient(ellipse at top left, #00D4AA, transparent)' }}
      />
      <div
        className="absolute bottom-0 right-0 w-64 h-64 opacity-[0.03]"
        style={{ background: 'radial-gradient(ellipse at bottom right, #60A5FA, transparent)' }}
      />
    </div>
  )
}
