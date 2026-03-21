import { Monitor, Check, X } from 'lucide-react';
import { Card } from '../ui/Card';
import { OutlineButton } from '../ui/OutlineButton';
import { bridge } from '../../lib/bridge';

const features = [
  { text: 'Full control over MT5', ok: true },
  { text: 'Uses your own machine', ok: true },
  { text: 'Requires PC always on', ok: false },
  { text: 'Windows only', ok: false },
];

export function LocalModeCard() {
  return (
    <Card>
      <div className="flex items-center gap-3 mb-5">
        <Monitor size={18} className="text-fg-soft shrink-0" />
        <h2 className="text-base font-semibold text-fg flex-1">Local Mode</h2>
        <span className="text-[0.6rem] font-bold text-fg-muted bg-bg-hover px-2.5 py-1 rounded-full uppercase tracking-wider shrink-0">
          Windows
        </span>
      </div>

      <div className="space-y-4 mb-auto flex-1">
        {features.map(({ text, ok }, i) => (
          <div key={i} className="flex items-center gap-3 text-sm text-fg-muted">
            {ok ? (
              <Check size={14} className="text-success transition-all duration-300 hover:scale-125" />
            ) : (
              <X size={14} className="text-danger transition-all duration-300 hover:scale-125" />
            )}
            {text}
          </div>
        ))}
      </div>

      <OutlineButton fullWidth onClick={() => bridge.openExternal('https://pub-c30cd5bbe12144caac83d4bd06bfbc6b.r2.dev/PlatAlgoRelay.exe')} className="mt-4">
        Download Windows Relay
      </OutlineButton>
    </Card>
  );
}
