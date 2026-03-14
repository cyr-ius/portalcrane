import { ChangeDetectionStrategy, Component, input } from '@angular/core';

@Component({
  selector: 'app-logo',
  imports: [],
  templateUrl: './app-logo.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppLogo {
  /** Width and height in pixels (square). Defaults to 48. */
  size = input<number>(48);
  style = input<string>('');
}
