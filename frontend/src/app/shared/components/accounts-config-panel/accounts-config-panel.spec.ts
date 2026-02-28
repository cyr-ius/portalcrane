import { ComponentFixture, TestBed } from "@angular/core/testing";

import { AccountsConfigPanel } from "./accounts-config-panel";

describe("AccountsConfigPanel", () => {
  let component: AccountsConfigPanel;
  let fixture: ComponentFixture<AccountsConfigPanel>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AccountsConfigPanel],
    }).compileComponents();

    fixture = TestBed.createComponent(AccountsConfigPanel);
    component = fixture.componentInstance;
    await fixture.whenStable();
  });

  it("should create", () => {
    expect(component).toBeTruthy();
  });
});
