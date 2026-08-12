(() => {
  const TERRAIN_COLORS = {
    grassA: "#778b50",
    grassB: "#6f844a",
    dirt: "#9b8259",
    stone: "#666d69",
  };
  const PAWN_COLORS = ["#4f84c4", "#63a85b", "#d5a247", "#a966b5", "#5eaeb1"];
  const STRUCTURE_COLORS = {
    wall: "#4c392c",
    door: "#9c8b72",
    bed: "#ad8753",
    table: "#806043",
    campfire: "#e86e2b",
    workbench: "#677a7c",
    storage: "#707673",
  };

  class ColonyCanvasRenderer {
    constructor(canvas) {
      this.canvas = canvas;
      this.context = canvas.getContext("2d");
      this.size = { width: 1, height: 1, dpr: 1 };
    }

    pawnColor(index) {
      return PAWN_COLORS[index % PAWN_COLORS.length];
    }

    cellAt(clientX, clientY, state) {
      const rect = this.canvas.getBoundingClientRect();
      const width = Number(state.map?.width || 48);
      const height = Number(state.map?.height || 32);
      return {
        x: Math.max(0, Math.min(width - 1, Math.floor((clientX - rect.left) / rect.width * width))),
        y: Math.max(0, Math.min(height - 1, Math.floor((clientY - rect.top) / rect.height * height))),
      };
    }

    draw(state, selection) {
      if (!state || !this.context) return;
      this.#fitCanvas();
      const mapWidth = Number(state.map?.width || 48);
      const mapHeight = Number(state.map?.height || 32);
      const cellWidth = this.canvas.width / mapWidth;
      const cellHeight = this.canvas.height / mapHeight;
      this.context.clearRect(0, 0, this.canvas.width, this.canvas.height);
      this.#drawTerrain(state, mapWidth, mapHeight, cellWidth, cellHeight);
      for (const structure of state.structures || []) {
        this.#drawStructure(structure, cellWidth, cellHeight);
      }
      for (const blueprint of state.blueprints || []) {
        this.#drawBlueprint(blueprint, cellWidth, cellHeight);
      }
      (state.colonists || []).forEach((colonist, index) => {
        this.#drawPawn(
          colonist,
          index,
          cellWidth,
          cellHeight,
          false,
          selection.colonistId
        );
      });
      if (state.raid) {
        this.#drawPawn(
          { ...state.raid, name: "Raider" },
          0,
          cellWidth,
          cellHeight,
          true,
          selection.colonistId
        );
      }
      this.#drawSelection(selection.cell, cellWidth, cellHeight);
    }

    #terrainAt(x, y, width, height, seed) {
      const centerX = width * 0.48;
      const centerY = height * 0.53;
      const dx = x - centerX;
      const dy = y - centerY;
      if ((x < width * .18 && y < height * .22) || (x > width * .82 && y > height * .78)) {
        return "stone";
      }
      if ((dx * dx) / (width * 4.8) + (dy * dy) / (height * 2.8) < 1) return "dirt";
      return ((x * 17 + y * 31 + seed * 3) % 9 < 4) ? "grassA" : "grassB";
    }

    #fitCanvas() {
      const rect = this.canvas.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const width = Math.max(1, Math.round(rect.width * dpr));
      const height = Math.max(1, Math.round(rect.height * dpr));
      if (this.canvas.width !== width || this.canvas.height !== height) {
        this.canvas.width = width;
        this.canvas.height = height;
      }
      this.size = { width, height, dpr };
    }

    #roundedRect(x, y, width, height, radius, fill, stroke = "#171b19", lineWidth = 1) {
      const context = this.context;
      context.beginPath();
      context.roundRect(x, y, width, height, radius);
      context.fillStyle = fill;
      context.fill();
      if (stroke) {
        context.strokeStyle = stroke;
        context.lineWidth = lineWidth;
        context.stroke();
      }
    }

    #drawTerrain(state, mapWidth, mapHeight, cellWidth, cellHeight) {
      const context = this.context;
      const seed = Number(state.seed || 1);
      for (let y = 0; y < mapHeight; y += 1) {
        for (let x = 0; x < mapWidth; x += 1) {
          const terrain = this.#terrainAt(x, y, mapWidth, mapHeight, seed);
          context.fillStyle = TERRAIN_COLORS[terrain];
          context.fillRect(
            x * cellWidth,
            y * cellHeight,
            Math.ceil(cellWidth + .5),
            Math.ceil(cellHeight + .5)
          );
          if ((x * 11 + y * 7 + seed) % 17 === 0) {
            context.fillStyle = terrain === "stone" ? "#ffffff0b" : "#ffffff10";
            context.fillRect(
              (x + .18) * cellWidth,
              (y + .18) * cellHeight,
              Math.max(1, cellWidth * .2),
              Math.max(1, cellHeight * .2)
            );
          }
        }
      }
    }

    #drawStructure(structure, cellWidth, cellHeight) {
      const context = this.context;
      const kind = String(structure.kind || "structure");
      const x = Number(structure.x || 0) * cellWidth;
      const y = Number(structure.y || 0) * cellHeight;
      const unit = Math.min(cellWidth, cellHeight);
      if (kind === "bed") {
        this.#roundedRect(
          x - cellWidth * .28,
          y - cellHeight * .5,
          cellWidth * 1.55,
          cellHeight * 2,
          unit * .15,
          STRUCTURE_COLORS.bed
        );
        this.#roundedRect(
          x - cellWidth * .12,
          y - cellHeight * .36,
          cellWidth * 1.23,
          cellHeight * .42,
          unit * .1,
          "#ddd7c6",
          null
        );
        return;
      }
      if (kind === "campfire") {
        context.fillStyle = "#4f5652";
        context.beginPath();
        context.arc(x + cellWidth * .5, y + cellHeight * .5, unit * .62, 0, Math.PI * 2);
        context.fill();
        context.fillStyle = STRUCTURE_COLORS.campfire;
        context.beginPath();
        context.arc(x + cellWidth * .5, y + cellHeight * .5, unit * .34, 0, Math.PI * 2);
        context.fill();
        return;
      }
      this.#roundedRect(
        x + cellWidth * .05,
        y + cellHeight * .05,
        cellWidth * .9,
        cellHeight * .9,
        unit * .12,
        STRUCTURE_COLORS[kind] || "#6e716b"
      );
    }

    #drawBlueprint(blueprint, cellWidth, cellHeight) {
      const context = this.context;
      const x = Number(blueprint.x || 0) * cellWidth;
      const y = Number(blueprint.y || 0) * cellHeight;
      const progress = Math.max(0, Math.min(1, Number(blueprint.progress || 0)));
      context.save();
      context.strokeStyle = "#aee3f4";
      context.lineWidth = Math.max(1.5, this.size.dpr);
      context.setLineDash([5 * this.size.dpr, 4 * this.size.dpr]);
      context.strokeRect(x + 2, y + 2, cellWidth - 4, cellHeight - 4);
      context.setLineDash([]);
      context.fillStyle = "#bcecff28";
      context.fillRect(
        x + 2,
        y + cellHeight * (1 - progress),
        cellWidth - 4,
        cellHeight * progress - 2
      );
      context.restore();
    }

    #drawPawn(pawn, index, cellWidth, cellHeight, hostile, selectedColonistId) {
      const context = this.context;
      const centerX = (Number(pawn.x || 0) + .5) * cellWidth;
      const centerY = (Number(pawn.y || 0) + .5) * cellHeight;
      const size = Math.max(8, Math.min(cellWidth, cellHeight) * .78);
      const selected = !hostile && selectedColonistId === String(pawn.id || "");
      context.save();
      context.translate(centerX, centerY);
      context.shadowColor = "#0008";
      context.shadowOffsetX = size * .18;
      context.shadowOffsetY = size * .22;
      context.shadowBlur = size * .12;
      if (selected) {
        context.strokeStyle = "#ffe09a";
        context.lineWidth = Math.max(2, size * .16);
        context.beginPath();
        context.arc(0, 0, size * .88, 0, Math.PI * 2);
        context.stroke();
      }
      const color = hostile ? "#a83b35" : this.pawnColor(index);
      context.fillStyle = pawn.waiting ? "#d6a52e" : pawn.mental_break ? "#d8554a" : color;
      context.strokeStyle = "#18201d";
      context.lineWidth = Math.max(1, size * .11);
      context.beginPath();
      context.roundRect(-size * .46, -size * .12, size * .92, size * 1.02, size * .22);
      context.fill();
      context.stroke();
      context.shadowColor = "transparent";
      context.fillStyle = "#efc38f";
      context.beginPath();
      context.arc(0, -size * .4, size * .34, 0, Math.PI * 2);
      context.fill();
      context.stroke();
      context.fillStyle = "#26302c";
      const facing = hostile ? -1 : 1;
      context.fillRect(facing * size * .28, size * .04, facing * size * .7, size * .13);
      context.restore();
      if (cellWidth < 14 || cellHeight < 14) return;
      context.save();
      context.font = `700 ${Math.max(9, size * .5)}px system-ui`;
      context.textAlign = "center";
      context.fillStyle = "#fff";
      context.strokeStyle = "#000";
      context.lineWidth = 3;
      const name = String(pawn.name || (hostile ? "Raider" : "?"));
      context.strokeText(name, centerX, centerY + size * 1.45);
      context.fillText(name, centerX, centerY + size * 1.45);
      context.restore();
    }

    #drawSelection(selectedCell, cellWidth, cellHeight) {
      if (!selectedCell) return;
      const context = this.context;
      context.strokeStyle = "#fff2ba";
      context.lineWidth = Math.max(2, this.size.dpr * 1.5);
      context.strokeRect(
        selectedCell.x * cellWidth + 1,
        selectedCell.y * cellHeight + 1,
        cellWidth - 2,
        cellHeight - 2
      );
    }
  }

  window.ColonyCanvasRenderer = ColonyCanvasRenderer;
})();
