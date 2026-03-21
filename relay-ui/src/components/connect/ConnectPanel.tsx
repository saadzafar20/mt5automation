import { ScrollReveal } from '../ui/ScrollReveal';
import { SignInCard } from './SignInCard';
import { MT5LoginCard } from './MT5LoginCard';
import { VPSCard } from './VPSCard';
import { LocalModeCard } from './LocalModeCard';

export function ConnectPanel() {
  return (
    <div className="h-full flex flex-col">
      <ScrollReveal variant="fade-up">
        <h1 className="text-2xl font-bold text-fg mb-1">Connect</h1>
        <p className="text-sm text-fg-muted mb-4">Sign in and choose your execution mode</p>
      </ScrollReveal>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 flex-1">
        {/* Left column */}
        <div className="flex flex-col gap-5">
          <ScrollReveal variant="slide-left" delay={0.1} className="flex-1">
            <SignInCard />
          </ScrollReveal>
          <ScrollReveal variant="slide-left" delay={0.2} className="flex-1">
            <MT5LoginCard />
          </ScrollReveal>
        </div>

        {/* Right column */}
        <div className="flex flex-col gap-5">
          <ScrollReveal variant="slide-right" delay={0.15} className="flex-[2]">
            <VPSCard />
          </ScrollReveal>
          <ScrollReveal variant="slide-right" delay={0.25} className="flex-1">
            <LocalModeCard />
          </ScrollReveal>
        </div>
      </div>
    </div>
  );
}
