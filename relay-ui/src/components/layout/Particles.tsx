import { useMemo } from 'react';

export function Particles() {
  const particles = useMemo(() => {
    return Array.from({ length: 20 }, (_, i) => ({
      id: i,
      left: `${Math.random() * 100}%`,
      size: 2 + Math.random() * 3,
      duration: 15 + Math.random() * 25,
      delay: Math.random() * 20,
      opacity: 0.15 + Math.random() * 0.35,
    }));
  }, []);

  return (
    <div className="fixed inset-0 pointer-events-none overflow-hidden z-0">
      {/* Ambient orbs */}
      <div
        className="absolute w-[600px] h-[600px] rounded-full"
        style={{
          background: 'var(--color-primary-glow)',
          filter: 'blur(200px)',
          top: '10%',
          left: '5%',
          animation: 'float-orb 20s ease-in-out infinite',
        }}
      />
      <div
        className="absolute w-[500px] h-[500px] rounded-full"
        style={{
          background: 'var(--color-accent-glow)',
          filter: 'blur(180px)',
          bottom: '10%',
          right: '5%',
          animation: 'float-orb 25s ease-in-out infinite reverse',
        }}
      />
      <div
        className="absolute w-[300px] h-[300px] rounded-full"
        style={{
          background: 'var(--color-primary-glow)',
          filter: 'blur(120px)',
          top: '60%',
          left: '40%',
          animation: 'float-orb 18s ease-in-out infinite 5s',
        }}
      />

      {/* Small floating particles */}
      {particles.map((p) => (
        <div
          key={p.id}
          className="absolute rounded-full"
          style={{
            left: p.left,
            bottom: '-10px',
            width: p.size,
            height: p.size,
            background: p.id % 3 === 0 ? 'var(--color-accent)' : 'var(--color-primary)',
            opacity: 0,
            animation: `drift-particle ${p.duration}s linear ${p.delay}s infinite`,
          }}
        />
      ))}
    </div>
  );
}
