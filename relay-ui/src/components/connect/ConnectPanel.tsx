import { ScrollReveal } from '../ui/ScrollReveal';
import { SignInCard } from './SignInCard';
import { MT5LoginCard } from './MT5LoginCard';
import { VPSCard } from './VPSCard';

export function ConnectPanel() {
  return (
    <div className="h-full flex flex-col">
      <ScrollReveal variant="fade-up">
        <h1 className="text-2xl font-bold text-fg mb-1" style={{ letterSpacing: '-0.02em' }}>Connect</h1>
        <p className="text-sm text-fg-muted mb-6">Sign in and configure your MT5 cloud connection</p>
      </ScrollReveal>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 flex-1 min-h-0">
        {/* Left column */}
        <div className="flex flex-col gap-5">
          <ScrollReveal variant="slide-left" delay={0.1}>
            <SignInCard />
          </ScrollReveal>
          <ScrollReveal variant="slide-left" delay={0.2}>
            <MT5LoginCard />
          </ScrollReveal>
        </div>

        {/* Right column */}
        <ScrollReveal variant="slide-right" delay={0.15} className="h-full">
          <VPSCard />
        </ScrollReveal>
      </div>
    </div>
  );
}
