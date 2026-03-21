import { ScrollReveal } from '../ui/ScrollReveal';
import { SignInCard } from './SignInCard';
import { MT5LoginCard } from './MT5LoginCard';
import { VPSCard } from './VPSCard';
import { LocalModeCard } from './LocalModeCard';

export function ConnectPanel() {
  return (
    <div className="max-w-full mx-auto space-y-6 h-full flex flex-col">
      <ScrollReveal variant="fade-up">
        <h1 className="text-2xl font-bold text-fg mb-1">Connect</h1>
        <p className="text-sm text-fg-muted">Sign in and choose your execution mode</p>
      </ScrollReveal>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 flex-1 min-h-0">
        {/* Left column: Sign In + MT5 Login (with cloud button) */}
        <div className="flex flex-col gap-6">
          <ScrollReveal variant="slide-left" delay={0.1} className="flex-1">
            <SignInCard />
          </ScrollReveal>
          <ScrollReveal variant="slide-left" delay={0.2} className="flex-1">
            <MT5LoginCard />
          </ScrollReveal>
        </div>

        {/* Right column: VPS suggestion + Local mode */}
        <div className="flex flex-col gap-6 overflow-hidden">
          <ScrollReveal variant="slide-right" delay={0.15} className="flex-1">
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
