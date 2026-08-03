import { BarChart3 } from "lucide-react";

import "./ComposerCommandMenu.css";

export type ComposerCommand = {
  id: "vote";
  command: string;
  label: string;
  description: string;
};

export const COMPOSER_COMMANDS: ComposerCommand[] = [
  {
    id: "vote",
    command: "/vote",
    label: "투표 만들기",
    description: "질문, 선택지, 투표 시간을 설정합니다.",
  },
];

export function matchingComposerCommands(message: string): ComposerCommand[] {
  const match = message.match(/^\s*(\/[^\s]*)$/);
  if (!match) return [];
  const query = match[1].toLocaleLowerCase();
  return COMPOSER_COMMANDS.filter((item) =>
    `${item.command} ${item.label}`.toLocaleLowerCase().includes(query)
  );
}

export default function ComposerCommandMenu({
  listId,
  commands,
  activeIndex,
  onActiveIndexChange,
  onSelect,
}: {
  listId: string;
  commands: ComposerCommand[];
  activeIndex: number;
  onActiveIndexChange: (index: number) => void;
  onSelect: (command: ComposerCommand) => void;
}) {
  return (
    <div className="dc-composer-command-menu" aria-label="채팅 명령" role="listbox" id={listId}>
      <small>명령</small>
      {commands.map((item, index) => (
        <button
          key={item.id}
          id={`${listId}-option-${index}`}
          type="button"
          role="option"
          aria-selected={index === activeIndex}
          data-active={index === activeIndex}
          onMouseDown={(event) => event.preventDefault()}
          onMouseEnter={() => onActiveIndexChange(index)}
          onClick={() => onSelect(item)}
        >
          <span className="dc-composer-command-icon" aria-hidden="true">
            <BarChart3 size={17} />
          </span>
          <span className="dc-composer-command-copy">
            <strong>{item.command}</strong>
            <span>{item.label}</span>
            <small>{item.description}</small>
          </span>
        </button>
      ))}
    </div>
  );
}
